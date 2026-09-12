"""Check prepared packs with ComfyUI on PYTHONPATH; accepts any number of pack paths."""
import argparse
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys
import types
from unittest import mock

from aiohttp import web
import torch


class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'trimesh','pyrender','OpenGL','pyglet','av'}:
            raise ModuleNotFoundError(f'Optional dependency blocked: {fullname}', name=fullname)


def load(path, name):
    spec=importlib.util.spec_from_file_location(name,path/'__init__.py',submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module)
    return module


def contract(node):
    inputs=node.INPUT_TYPES()
    return {'category':node.CATEGORY,'function':node.FUNCTION,'returns':list(node.RETURN_TYPES),
            'return_names':list(getattr(node,'RETURN_NAMES',node.RETURN_TYPES)),
            'inputs':{kind:[[name, 'COMBO' if isinstance(value[0],list) else value[0]] for name,value in values.items()] for kind,values in inputs.items()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packages',nargs='+',type=Path)
    parser.add_argument('--source',type=Path)
    args=parser.parse_args()
    import comfy.cli_args
    comfy.cli_args.args.cpu=True
    import comfy.utils
    import comfy.model_management
    import folder_paths
    instance=types.SimpleNamespace(routes=web.RouteTableDef())
    registered={}; contracts={}; extensions=set()
    blocker=BlockOptional(); sys.meta_path.insert(0,blocker)
    try:
        with mock.patch.dict(sys.modules,{'server':types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=instance))}), mock.patch('subprocess.Popen',side_effect=AssertionError('Import launched a process')), mock.patch('threading.Thread.start',side_effect=AssertionError('Import launched a thread')), mock.patch.object(comfy.model_management,'unload_all_models',side_effect=AssertionError('Import unloaded models')):
            for i,path in enumerate(args.packages):
                path=path.resolve()
                module=load(path,f'pack_{i}')
                manifest=json.loads((path/'node-ownership.json').read_text())
                assert set(module.NODE_CLASS_MAPPINGS)==set(manifest['nodes']),path
                for node_id,node in module.NODE_CLASS_MAPPINGS.items():
                    assert node_id not in registered, f'Duplicate node: {node_id}'
                    registered[node_id]=path.name; contracts[node_id]=contract(node)
                directory=getattr(module,'WEB_DIRECTORY',None)
                if directory:
                    for entry in (path/directory).rglob('*.js'):
                        assert 'vendor' not in entry.parts, f'Vendor auto-loaded: {entry}'
                        if entry.name.startswith('validate_'): continue
                        assert entry.name not in extensions, f'Duplicate frontend entrypoint: {entry}'
                        extensions.add(entry.name)
            routes=[(route.method,route.path) for route in instance.routes]
            assert len(routes)==len(set(routes)),f'Duplicate routes: {routes}'
            for module in list(sys.modules.values()):
                if isinstance(module,types.ModuleType) and module.__name__.endswith('.wn_gguf_server'):
                    assert module.SERVER_MANAGER._handle is None
            if args.source:
                original=load(args.source.resolve(),'legacy_wepenerd')
                expected={k:contract(v) for k,v in original.NODE_CLASS_MAPPINGS.items()}
                assert expected==contracts,'Legacy node contracts changed or ownership is incomplete'
    finally:
        sys.meta_path.remove(blocker)
    print(json.dumps({'nodes':registered,'routes':routes,'frontend_entrypoints':sorted(extensions),'legacy_contracts':bool(args.source)},indent=2))


if __name__=='__main__': main()
