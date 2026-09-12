import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sp = importlib.import_module('wepenerd_testpkg.speedpaint_node')


class BinaryCommitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.paths = mock.patch.object(sp, 'asset_root', return_value=Path(self.directory.name))
        self.paths.start()
        instance = types.SimpleNamespace(routes=web.RouteTableDef())
        with mock.patch.dict(sys.modules, {'server': types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=instance))}):
            sp.register_speedpaint_routes()
            sp.register_speedpaint_routes()
        self.assertEqual(len(instance.routes), 1)
        app = web.Application(client_max_size=sp.MAX_BYTES*2)
        app.add_routes(instance.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.paths.stop()
        self.directory.cleanup()

    async def commit(self, image, document=None):
        stream = io.BytesIO(); image.save(stream, format='PNG')
        form = FormData()
        form.add_field('image', stream.getvalue(), filename='painting.png', content_type='image/png')
        form.add_field('document', json.dumps(document or {'v':1,'width':64,'height':64,'background':'#fff000','painted':True}))
        return await self.client.post('/wepenerd/speedpaint/commit', data=form)

    async def test_binary_roundtrip_and_portability(self):
        image = Image.new('RGB',(64,64),'#123456')
        response = await self.commit(image)
        self.assertEqual(response.status,200,await response.text())
        document = await response.json()
        self.assertNotIn('inline',document)
        self.assertEqual(sp.prepare_image(document,64,64).tobytes(),image.tobytes())
        again = await (await self.commit(image)).json()
        self.assertEqual(document['asset'],again['asset'])
        with tempfile.TemporaryDirectory() as transferred:
            asset = sp.asset_path(document['asset']).read_bytes()
            Path(transferred,document['asset']).write_bytes(asset)
            with mock.patch.object(sp,'asset_root',return_value=Path(transferred)):
                self.assertEqual(sp.prepare_image(document,64,64).tobytes(),image.tobytes())

    async def test_reject_wrong_dimensions_and_alpha(self):
        for image in [Image.new('RGB',(65,64)),Image.new('RGBA',(64,64),(0,0,0,128))]:
            response = await self.commit(image)
            self.assertEqual(response.status,400)

    async def test_chunked_limit_and_invalid_document(self):
        with mock.patch.object(sp,'MAX_BYTES',100):
            response = await self.commit(Image.effect_noise((64,64),100).convert('RGB'))
            self.assertEqual(response.status,400)
        response = await self.commit(Image.new('RGB',(64,64)),{'v':999,'width':64,'height':64})
        self.assertEqual(response.status,400)
