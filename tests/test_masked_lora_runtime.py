"""Lifecycle, cache and ownership regressions for the masked adapter runtime."""

import gc
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import weakref

import torch
import torch.nn.functional as F

import test_masked_lora as fixtures
from wepenerd_lora_test import masked_lora_runtime as rt

ml = fixtures.ml


def layout(batch=1, labels=(0,), shape=(4, 6)):
    geometry = ml.ModelGeometry(*shape, 2, 2, (shape[0] // 2) * (shape[1] // 2), 0)
    return ml.call_layout(batch * len(labels), {"cond_or_uncond": labels}, geometry, (batch, shape, max(labels) + 1))


class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(42)
        self.data = ml.AdapterData(("file", "layer", "lora"), fixtures.parse(fixtures.lora_weights()))

    def test_resident_conversion_shared_across_regions_and_actual_dtype(self):
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(cache_bytes=4096, headroom_bytes=0))
        first = runtime.adapter(self.data, "cpu", torch.float64)
        same_content = ml.AdapterData(self.data.identity, fixtures.parse(fixtures.lora_weights()))
        self.assertIs(first, runtime.adapter(same_content, torch.device("cpu"), torch.float64))
        self.assertEqual(runtime.stats["conversions"], 2)
        self.assertEqual(runtime.stats["adapter_hits"], 1)
        single = runtime.adapter(self.data, "cpu", torch.float32)
        self.assertIsNot(first, single)
        self.assertEqual(single.weights[0].dtype, torch.float32)
        refs = [weakref.ref(v) for v in first.weights if isinstance(v, torch.Tensor)]
        del first
        runtime.close()
        gc.collect()
        self.assertTrue(all(ref() is None for ref in refs))
        self.assertEqual(runtime.resident_bytes, 0)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            runtime.adapter(self.data, "cpu", torch.float64)

    def test_eviction_and_streaming_remain_bounded_and_equal(self):
        other = ml.AdapterData(("other", "layer", "lora"), fixtures.parse(fixtures.lora_weights(alpha=5)))
        x = torch.rand(1, 6, 12, dtype=torch.float64)
        for budget in (0, 300, 10000):
            runtime = rt.SamplingRuntime(rt.RuntimePolicy(budget, 0))
            for data in (self.data, other, self.data):
                actual = runtime.adapter(data, "cpu", x.dtype).h(x, torch.zeros(1, 6, 6))
                reference = fixtures.reference_weight(torch.nn.Linear(12, 6, bias=False).double(), [])
                # Independent zero-weight weight-path reference, not another bypass.
                reference.zero_()
                reference = fixtures.comfy.lora.calculate_weight([(1., data.native, 1., None, None)], reference, "layer")
                torch.testing.assert_close(actual, F.linear(x, reference), atol=1e-7, rtol=1e-5)
                self.assertLessEqual(runtime.resident_bytes, budget)
            self.assertEqual(runtime.stats["conversions"], 6 if budget == 0 else 4)
            if budget == 0:
                self.assertEqual(runtime.stats["streamed"], 3)
            if budget == 300:
                self.assertEqual(runtime.stats["evictions"], 0)
                self.assertEqual(runtime.stats["adapter_hits"], 1)
            runtime.close()

    def test_headroom_rejects_allocation_before_conversion(self):
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(4096, 1024), free_memory=lambda device: 1025)
        with patch.object(torch.Tensor, "to", side_effect=AssertionError("allocation happened")):
            with self.assertRaisesRegex(RuntimeError, "working memory"):
                runtime.adapter(self.data, "cuda:0", torch.float32)

    def test_cyclic_overflow_keeps_two_adapters_and_shared_mask_resident(self):
        data = [ml.AdapterData((i,), self.data.native) for i in range(6)]
        size = sum(v.numel()*8 for v in self.data.native.weights if isinstance(v, torch.Tensor))
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(2*size + 48, 0))
        spec = fixtures.region_spec(torch.ones(4, 6))
        for _ in range(4):
            for adapter in data:
                runtime.mask(spec, layout(), "cpu", torch.float64, ml.project_mask)
                runtime.adapter(adapter, "cpu", torch.float64)
                self.assertLessEqual(runtime.resident_bytes, runtime.policy.cache_bytes)
        self.assertEqual(runtime.stats["adapter_hits"], 6)
        self.assertEqual(runtime.stats["conversions"], 36)
        self.assertEqual(runtime.stats["mask_projections"], 1)
        self.assertEqual(runtime.stats["mask_hits"], 23)
        self.assertEqual(runtime.stats["evictions"], 0)
        runtime.close()

    def test_pressure_checks_once_when_healthy_and_can_evict_residents(self):
        free = Mock(return_value=1000)
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(100, 0), free_memory=free)
        runtime._store(("first",), object(), 80)
        runtime._make_room(40, torch.device("cuda"))
        self.assertEqual(free.call_count, 1)
        self.assertEqual(runtime.resident_bytes, 80)
        free.side_effect = [10, 1000]
        runtime._make_room(40, torch.device("cuda"))
        self.assertEqual(runtime.stats["evictions"], 1)
        self.assertEqual(runtime.resident_bytes, 0)

    def check_mask_admission_after_layout_change(self, device):
        # Old mask + two adapters fill the cap; the larger new layout needs room.
        dtype = torch.float64
        size = sum(v.numel()*8 for v in self.data.native.weights if isinstance(v, torch.Tensor))
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(2*size + 48, 0))
        self.addCleanup(runtime.close)
        spec = fixtures.region_spec(torch.ones(4, 6))
        original = runtime.mask(spec, layout(), device, dtype, ml.project_mask)
        first = runtime.adapter(self.data, device, dtype)
        other = ml.AdapterData(("other",), self.data.native)
        second = runtime.adapter(other, device, dtype)
        self.assertEqual(runtime.resident_bytes, runtime.policy.cache_bytes)
        projected = runtime.mask(spec, layout(2), device, dtype, ml.project_mask)
        self.assertEqual(runtime.stats["evictions"], 1)
        self.assertIs(original, runtime.mask(spec, layout(), device, dtype, ml.project_mask))
        self.assertIs(second, runtime.adapter(other, device, dtype))
        for _ in range(5):
            runtime.adapter(self.data, device, dtype)  # Displaced adapter streams.
            self.assertIs(projected, runtime.mask(spec, layout(2), device, dtype, ml.project_mask))
            self.assertLessEqual(runtime.resident_bytes, runtime.policy.cache_bytes)
        self.assertEqual(runtime.stats["mask_projections"], 2)
        self.assertEqual(runtime.stats["mask_transfer_bytes"], 144 if device == "cuda" else 0)
        self.assertEqual(runtime.stats["evictions"], 1)
        self.assertIsNot(first, runtime.adapter(self.data, device, dtype))

    def test_new_mask_displaces_adapter_and_survives_streamed_layer_visits(self):
        self.check_mask_admission_after_layout_change("cpu")

    @unittest.skipUnless(os.environ.get("WEPENERD_TEST_CUDA") == "1", "set WEPENERD_TEST_CUDA=1 for mask admission")
    def test_cuda_new_mask_transfers_once_after_layout_change(self):
        self.check_mask_admission_after_layout_change("cuda")

    def test_mask_admission_respects_entry_byte_and_disabled_limits(self):
        spec = fixtures.region_spec(torch.ones(4, 6))
        for policy in (rt.RuntimePolicy(0, 0), rt.RuntimePolicy(47, 0), rt.RuntimePolicy(4096, 0, 0)):
            runtime = rt.SamplingRuntime(policy)
            for _ in range(2):
                runtime.mask(spec, layout(), "cpu", torch.float64, ml.project_mask)
            self.assertFalse(runtime.entries)
            self.assertEqual(runtime.stats["mask_projections"], 2)
            runtime.close()
        for policy in (rt.RuntimePolicy(4096, 0, 2), rt.RuntimePolicy(144, 0)):
            runtime = rt.SamplingRuntime(policy)
            first = runtime.mask(spec, layout(), "cpu", torch.float64, ml.project_mask)
            runtime._store(("adapter", "test"), object(), 1)
            second = runtime.mask(spec, layout(2), "cpu", torch.float64, ml.project_mask)
            self.assertIs(first, runtime.mask(spec, layout(), "cpu", torch.float64, ml.project_mask))
            self.assertIs(second, runtime.mask(spec, layout(2), "cpu", torch.float64, ml.project_mask))
            # Mask-only overflow evicts old masks, keeping the same bounds.
            third = runtime.mask(spec, layout(shape=(2, 2)), "cpu", torch.float64, ml.project_mask)
            self.assertIs(third, runtime.mask(spec, layout(shape=(2, 2)), "cpu", torch.float64, ml.project_mask))
            self.assertLessEqual(runtime.resident_bytes, policy.cache_bytes)
            self.assertLessEqual(len(runtime.entries), policy.max_entries)
            self.assertEqual(runtime.stats["evictions"], 2)
            runtime.close()

    def test_mask_batch_truncation_warns_once_per_run(self):
        runtime = rt.SamplingRuntime()
        spec = fixtures.region_spec(torch.ones(3, 4, 6))
        with self.assertLogs(level="WARNING") as logs:
            for _ in range(4):
                runtime.mask(spec, layout(2), "cpu", torch.float32, ml.project_mask)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("first 2 mask rows", logs.output[0])
        runtime.close()
        self.assertFalse(runtime.truncated_masks)

    def test_invalid_environment_values_warn_and_use_defaults(self):
        for value in ("abc", "-2", "2.5", ""):
            with patch.dict(os.environ, {"WEPENERD_MASKED_LORA_FILE_CACHE_MB": value}), self.assertLogs(level="WARNING"):
                self.assertEqual(rt.env_megabytes("WEPENERD_MASKED_LORA_FILE_CACHE_MB", 256), 256*rt.MIB)

    def test_projected_masks_share_cache_without_sigma_and_preserve_mapping(self):
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(4096, 0))
        spec = fixtures.region_spec(torch.stack((torch.zeros(4, 6), torch.ones(4, 6))))
        call = layout(3, (1, 0))
        mask = runtime.mask(spec, call, "cpu", torch.float32, ml.project_mask)
        self.assertEqual(mask[:, 0, 0].tolist(), [0, 1, 0, 0, 1, 0])
        self.assertIs(mask, runtime.mask(spec, call, "cpu", torch.float32, ml.project_mask))
        self.assertEqual(runtime.stats["mask_projections"], 1)
        # A split call changes row mapping; different geometry changes projection.
        runtime.mask(spec, layout(3), "cpu", torch.float32, ml.project_mask)
        runtime.mask(spec, layout(3, shape=(6, 4)), "cpu", torch.float32, ml.project_mask)
        runtime.mask(spec, call, "cpu", torch.float64, ml.project_mask)
        self.assertEqual(runtime.stats["mask_projections"], 4)
        runtime.close()

    def test_truncated_zero_mask_skips_projection_and_adapter_work_after_first_use(self):
        runtime = rt.SamplingRuntime(rt.RuntimePolicy(0, 0))
        spec = fixtures.region_spec(torch.stack((torch.zeros(4, 6), torch.ones(4, 6))))
        for _ in range(4):
            self.assertIsNone(runtime.mask(spec, layout(1), "cpu", torch.float32, ml.project_mask))
        self.assertEqual(runtime.stats["mask_projections"], 1)
        self.assertEqual(runtime.stats["conversions"], 0)
        runtime.close()

    def test_run_cleanup_success_exception_interruption_nested_and_repeated(self):
        context = ml.Krea2Context(rt.RuntimePolicy(4096, 0))
        runs, refs = [], []
        def allocate():
            runtime = context.runtime.get()
            runs.append(runtime)
            value = runtime.adapter(self.data, "cpu", torch.float64)
            refs.append(weakref.ref(value.weights[0]))
        def outer():
            allocate()
            parent = context.runtime.get()
            context.outer_sample(allocate)
            self.assertIs(context.runtime.get(), parent)
            self.assertFalse(parent.closed)
        context.outer_sample(outer)
        for error in (ValueError("test"), KeyboardInterrupt()):
            def fail():
                allocate()
                raise error
            with self.assertRaises(type(error)):
                context.outer_sample(fail)
        context.outer_sample(allocate)
        gc.collect()
        self.assertIsNone(context.runtime.get())
        self.assertTrue(all(run.closed and not run.entries and run.resident_bytes == 0 for run in runs))
        self.assertTrue(all(ref() is None for ref in refs))

    @unittest.skipUnless(os.environ.get("WEPENERD_TEST_CUDA") == "1", "set WEPENERD_TEST_CUDA=1 for CUDA residency")
    def test_cuda_resident_transfers_once_and_cleanup(self):
        self.assertTrue(torch.cuda.is_available())
        context = ml.Krea2Context()
        refs, runs = [], []
        def sample():
            runtime = context.runtime.get()
            runs.append(runtime)
            for _ in range(5):
                adapter = runtime.adapter(self.data, torch.device("cuda:0"), torch.bfloat16)
                refs.append(weakref.ref(adapter.weights[0]))
            self.assertEqual(runtime.stats["host_to_device_copies"], 2)
            self.assertEqual(runtime.stats["host_to_device_bytes"], (6 * 2 + 2 * 12) * 2)
        for _ in range(2):
            context.outer_sample(sample)
        def nested():
            sample()
            outer = context.runtime.get()
            context.outer_sample(sample)
            self.assertIs(context.runtime.get(), outer)
        context.outer_sample(nested)
        for error in (ValueError("test"), KeyboardInterrupt()):
            def fail():
                sample()
                raise error
            with self.assertRaises(type(error)):
                context.outer_sample(fail)
        torch.cuda.synchronize()
        gc.collect()
        self.assertIsNone(context.runtime.get())
        self.assertTrue(all(ref() is None for ref in refs))
        self.assertTrue(all(run.closed and not run.entries for run in runs))


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(12)
        self.context = ml.Krea2Context(rt.RuntimePolicy(10000, 0))
        self.context.current.set(layout(2))
        self.layer = torch.nn.Linear(12, 6)
        self.x = torch.randn(2, 8, 12) * .1
        self.adapters = [fixtures.parse(fixtures.lora_weights(alpha=5)), fixtures.parse(fixtures.lokr_weights(True, True, 3))]
        self.contributions = [fixtures.contribution(a, torch.full((4, 6), mask), strength)
                              for a, mask, strength in zip(self.adapters, (.7, .4), (-.2, .5))]

    def test_one_base_call_one_copy_and_independent_sum(self):
        calls = []
        def original(x):
            calls.append(x)
            return self.layer(x)
        dispatcher = ml.LayerDispatcher(original, self.contributions, self.context)
        def sample():
            for _ in range(3):
                output = dispatcher(self.x)
                expected = self.layer(self.x)
                weight = fixtures.reference_weight(self.layer, list(zip(self.adapters, (-.2 * .7, .5 * .4))))
                expected[:, 2:] = F.linear(self.x[:, 2:], weight, self.layer.bias)
                torch.testing.assert_close(output, expected, atol=1e-5, rtol=1e-4)
            self.assertEqual(len(calls), 3)
            self.assertEqual(self.context.runtime.get().stats["output_copies"], 3)
            self.assertEqual(self.context.runtime.get().stats["mask_projections"], 2)
        self.context.outer_sample(sample)

    def test_alias_view_and_retained_buffer_forwards_are_never_mutated(self):
        retained = self.layer(self.x).detach()
        cases = ((lambda x: x[..., :6], self.x[..., :6]), (lambda x: retained, retained))
        for original, shared in cases:
            before, input_before = shared.clone(), self.x.clone()
            dispatcher = ml.LayerDispatcher(original, self.contributions, self.context)
            output = self.context.outer_sample(lambda: dispatcher(self.x))
            torch.testing.assert_close(shared, before, atol=0, rtol=0)
            torch.testing.assert_close(self.x, input_before, atol=0, rtol=0)
            self.assertNotEqual(output.untyped_storage().data_ptr(), shared.untyped_storage().data_ptr())

    def test_native_private_inference_output_and_autograd_fallback(self):
        self.assertTrue(ml.native_private_output(self.layer, self.layer.forward))
        dispatcher = ml.LayerDispatcher(self.layer.forward, self.contributions, self.context, private_output=True)
        def sample():
            output = dispatcher(self.x)
            expected = self.layer(self.x)
            weight = fixtures.reference_weight(self.layer, list(zip(self.adapters, (-.2 * .7, .5 * .4))))
            expected[:, 2:] = F.linear(self.x[:, 2:], weight, self.layer.bias)
            torch.testing.assert_close(output, expected, atol=1e-5, rtol=1e-4)
            stats = self.context.runtime.get().stats
            self.assertEqual(stats["inplace_outputs"], int(not torch.is_grad_enabled()))
            self.assertEqual(stats["output_copies"], int(torch.is_grad_enabled()))
            return output
        with torch.set_grad_enabled(False):
            self.context.outer_sample(sample)
        self.context.outer_sample(sample).sum().backward()
        self.assertIsNotNone(self.layer.weight.grad)

    def test_zero_effect_skips_adapter_and_copy(self):
        contributions = (fixtures.contribution(self.adapters[0], torch.zeros(4, 6), 1.),
                         fixtures.contribution(self.adapters[1], torch.ones(4, 6), 0.))
        dispatcher = ml.LayerDispatcher(self.layer.forward, contributions, self.context)
        def sample():
            with patch.object(rt.SamplingRuntime, "adapter", side_effect=AssertionError("zero effect converted adapter")):
                output = dispatcher(self.x)
            torch.testing.assert_close(output, self.layer(self.x), atol=0, rtol=0)
            self.assertEqual(self.context.runtime.get().stats["output_copies"], 0)
        self.context.outer_sample(sample)

    def test_direct_use_outside_lifetime_fails(self):
        dispatcher = ml.LayerDispatcher(self.layer.forward, self.contributions, self.context)
        with self.assertRaisesRegex(RuntimeError, "OUTER_SAMPLE"):
            dispatcher(self.x)

    def check_active_rows(self, device, dtype):
        layer = torch.nn.Linear(12, 6).to(device=device, dtype=dtype)
        x = torch.randn(4, 8, 12, device=device, dtype=dtype)*.1
        context = ml.Krea2Context(rt.RuntimePolicy(10000, 0))
        context.current.set(layout(2, (1, 0)))
        for adapter in self.adapters:
            contribution = fixtures.contribution(adapter, torch.ones(4, 6), -.2)
            indices = torch.tensor([0, 3], device=device)
            context.gates.set({contribution.region.descriptor: indices})
            dispatcher = ml.LayerDispatcher(layer.forward, [contribution], context, private_output=True)
            def sample():
                prepared = context.runtime.get().adapter(contribution.adapter, x.device, x.dtype)
                prepared.h = Mock(wraps=prepared.h)
                actual = dispatcher(x)
                self.assertEqual(prepared.h.call_args[0][0].shape, (2, 6, 12))
                base = layer(x)
                # Batch size may change GEMM rounding; untouched rows must remain exact.
                delta = prepared.h(x[:, 2:], base[:, 2:])
                expected = base.clone()
                active = base[:, 2:].clone().addcmul_(delta, torch.ones(4, 6, 1, device=device, dtype=dtype), value=-.2)
                expected[[0, 3], 2:] = active[[0, 3]]
                tolerance = dict(atol=2e-3, rtol=2e-2) if dtype == torch.bfloat16 else dict(atol=1e-7, rtol=1e-6)
                torch.testing.assert_close(actual, expected, **tolerance)
                torch.testing.assert_close(actual[[1, 2]], base[[1, 2]], atol=0, rtol=0)
                return actual
            with torch.set_grad_enabled(False):
                context.outer_sample(sample)
            if device == "cpu":
                context.outer_sample(sample).sum().backward()
                self.assertIsNotNone(layer.weight.grad)

    def test_mixed_rows_only_compute_active_lora_lokr_and_preserve_equation(self):
        self.check_active_rows("cpu", torch.float32)

    @unittest.skipUnless(os.environ.get("WEPENERD_TEST_CUDA") == "1", "set WEPENERD_TEST_CUDA=1 for active-row CUDA")
    def test_cuda_active_rows_preserve_bfloat16_equation(self):
        self.check_active_rows("cuda", torch.bfloat16)


class FileCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "adapter.pt"
        torch.save(fixtures.lora_weights(), self.path)

    def test_hit_replacement_same_path_and_explicit_clear(self):
        cache = rt.AdapterFileCache()
        with patch.object(rt, "hash_file", wraps=rt.hash_file) as digest, \
             patch.object(ml, "read_adapter_file", wraps=ml.read_adapter_file) as parse:
            first = cache.load(self.path, parse)
            self.assertIs(first, cache.load(self.path, parse))
            self.assertEqual((digest.call_count, parse.call_count), (1, 1))
            replacement = self.path.with_suffix(".new")
            torch.save(fixtures.lora_weights(alpha=7), replacement)
            replacement.replace(self.path)
            second = cache.load(self.path, parse)
            self.assertNotEqual(first.digest, second.digest)
            cache.clear()
            cache.load(self.path, parse)
            self.assertEqual((digest.call_count, parse.call_count), (3, 3))

    def test_hashing_uses_python310_apis_and_multiple_chunks(self):
        raw = b"test bytes\0" * (rt.MIB//5)
        self.path.write_bytes(raw)
        with patch.object(hashlib, "file_digest", side_effect=AssertionError("3.11 API used"), create=True):
            self.assertEqual(rt.hash_file(self.path), hashlib.sha256(raw).hexdigest())

    def test_identical_files_share_cpu_tensors_bounded_eviction_and_streaming(self):
        cache = rt.AdapterFileCache(max_bytes=200, max_entries=2)
        other = self.path.with_name("other.pt")
        other.write_bytes(self.path.read_bytes())
        first = cache.load(self.path, ml.read_adapter_file)
        second = cache.load(other, ml.read_adapter_file)
        self.assertIs(first.adapters, second.adapters)
        self.assertEqual(len(cache.entries), 1)
        self.assertLessEqual(cache.resident_bytes, 200)
        zero = rt.AdapterFileCache(max_bytes=0)
        zero.load(self.path, ml.read_adapter_file)
        self.assertFalse(zero.entries)
        cache.clear()
        self.assertEqual(cache.resident_bytes, 0)

    def test_parser_failure_and_file_change_during_read(self):
        cache = rt.AdapterFileCache()
        with self.assertRaisesRegex(ValueError, "parse failed"):
            cache.load(self.path, lambda *args: (_ for _ in ()).throw(ValueError("parse failed")))
        self.assertFalse(cache.entries)
        count = 0
        def changing(path, digest):
            nonlocal count
            count += 1
            parsed = ml.read_adapter_file(path, digest)
            if count == 1:
                torch.save(fixtures.lora_weights(alpha=9), path)
            return parsed
        result = cache.load(self.path, changing)
        self.assertEqual(count, 2)
        self.assertEqual(result.signature, rt.file_signature(self.path))
        cache.clear()
        def always_changing(path, digest):
            parsed = ml.read_adapter_file(path, digest)
            stat = Path(path).stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
            return parsed
        with self.assertRaisesRegex(ValueError, "changed while reading"):
            cache.load(self.path, always_changing)
        self.assertFalse(cache.entries)
        def changing_error(path, digest):
            stat = Path(path).stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
            raise RuntimeError("incomplete file parse")
        with self.assertRaisesRegex(ValueError, "changed while reading"):
            cache.load(self.path, changing_error)


class BranchRuntimeTests(unittest.TestCase):
    setUp = fixtures.NativeIntegrationTests.setUp
    load = fixtures.NativeIntegrationTests.load

    def test_only_patched_layers_are_validated_and_no_mask_asset_is_written(self):
        self.model.get_model_object("diffusion_model.last.linear").quant_format = "unvalidated"
        with patch.object(ml, "validate_linear", wraps=ml.validate_linear) as validate:
            result, _ = self.load(mask=torch.ones(4, 6), weights=fixtures.lora_weights("diffusion_model.first", 32, 8))
            self.assertEqual(validate.call_count, 1)
            self.assertEqual(len(result.object_patches), 1)
        with self.assertRaisesRegex(ValueError, "native floating-point or INT8"):
            self.load(mask=torch.ones(4, 6))

    def test_consolidation_parent_isolation_order_and_one_wrapper_per_branch(self):
        a, _ = self.load(mask=torch.ones(4, 6), unique_id="a", strength=.3)
        b, _ = self.load(mask=torch.full((4, 6), .5), unique_id="b", strength=-.4)
        ab, _ = self.load(source=a, mask=torch.full((4, 6), .5), unique_id="b", strength=-.4)
        ba, _ = self.load(source=b, mask=torch.ones(4, 6), unique_id="a", strength=.3)
        self.assertFalse(ab.clone_has_same_weights(ba))
        key = "diffusion_model.first.forward"
        self.assertEqual(len(a.object_patches[key].contributions), 1)
        self.assertEqual(len(ab.object_patches[key].contributions), 2)
        self.assertIsNot(a.object_patches[key].context, ab.object_patches[key].context)
        for model in (a, b, ab, ba):
            contexts = {id(value.context) for value in model.object_patches.values() if isinstance(value, ml.LayerDispatcher)}
            self.assertEqual(len(contexts), 1)
            for kind in (fixtures.pe.WrappersMP.OUTER_SAMPLE, fixtures.pe.WrappersMP.CALC_COND_BATCH, fixtures.pe.WrappersMP.APPLY_MODEL, fixtures.pe.WrappersMP.DIFFUSION_MODEL):
                self.assertEqual(len(model.get_all_wrappers(kind)), 1)
        self.assertFalse(self.model.object_patches)

    def test_global_patches_and_region_are_additive_across_branch_switches(self):
        key = "diffusion_model.first.weight"
        layer = self.model.get_model_object(key[:-7])
        weight = layer.weight.detach().clone()
        adapter = fixtures.comfy.lora.load_lora(self.weights, fixtures.comfy.lora.model_lora_keys_unet(self.model.model, {}), log_missing=False)[key]
        global_model = self.model.clone()
        global_model.add_patches({key: adapter}, strength_patch=.25)
        region, _ = self.load(source=global_model, mask=torch.ones(4, 6), strength=-.4)
        sibling, _ = self.load(source=global_model, mask=torch.full((4, 6), .5), strength=.7)
        x = torch.randn(1, 6, 8) * .1
        for branch, scale in ((region, -.4), (sibling, .35), (region, -.4)):
            branch.patch_model(device_to=torch.device("cpu"))
            context = branch.object_patches["diffusion_model.first.forward"].context
            context.current.set(layout())
            try:
                with torch.set_grad_enabled(False):
                    output = context.outer_sample(lambda: layer(x))
                merged = fixtures.comfy.lora.calculate_weight([(.25, adapter, 1., None, None), (scale, adapter, 1., None, None)], weight.clone(), key)
                torch.testing.assert_close(output, F.linear(x, merged, layer.bias), atol=1e-5, rtol=1e-4)
            finally:
                branch.unpatch_model()
        torch.testing.assert_close(layer.weight, weight, atol=0, rtol=0)

    def test_mask_only_edits_reuse_parsing_and_is_changed_tracks_files_and_refresh(self):
        torch.save(self.weights, self.file)
        with patch.object(fixtures.folder_paths, "get_filename_list", return_value=["test.pt"]), \
             patch.object(fixtures.folder_paths, "get_full_path_or_raise", return_value=str(self.file)), \
             patch.object(ml, "save_asset"), patch.object(ml, "read_adapter_file", wraps=ml.read_adapter_file) as parser:
            before = ml.WepeNerdLoadLoraMasked.IS_CHANGED("test.pt")
            first, _ = ml.WepeNerdLoadLoraMasked().load(self.model, "test.pt", .5, mask=torch.ones(4, 6))
            second, _ = ml.WepeNerdLoadLoraMasked().load(self.model, "test.pt", -.4, mask=torch.full((4, 6), .3))
            self.assertEqual(parser.call_count, 1)
            key = "diffusion_model.first.forward"
            self.assertIs(first.object_patches[key].contributions[0].adapter, second.object_patches[key].contributions[0].adapter)
            ml.WepeNerdLoadLoraMasked.clear_file_cache()
            self.assertNotEqual(before, ml.WepeNerdLoadLoraMasked.IS_CHANGED("test.pt"))
            refreshed = ml.WepeNerdLoadLoraMasked.IS_CHANGED("test.pt")
            torch.save(self.weights | {"metadata.version": 2}, self.file)
            self.assertNotEqual(refreshed, ml.WepeNerdLoadLoraMasked.IS_CHANGED("test.pt"))

    def test_native_outer_sample_prepares_before_lazy_allocation_and_cleans_up(self):
        import comfy.latent_formats
        branch, _ = self.load(mask=torch.ones(4, 6))
        branch.model.latent_format = comfy.latent_formats.LatentFormat()
        context = branch.object_patches["diffusion_model.first.forward"].context
        runs = []
        def before_preparation(executor, *args, **kwargs):
            runtime = context.runtime.get()
            self.assertIsNotNone(runtime)
            self.assertFalse(runtime.entries)
            self.assertEqual(runtime.stats["conversions"], 0)
            self.assertIs(runtime.free_memory.__self__, branch)
            runs.append(runtime)
            return executor(*args, **kwargs)
        branch.add_wrapper_with_key(fixtures.pe.WrappersMP.PREPARE_SAMPLING, "test", before_preparation)
        guider = fixtures.comfy.samplers.CFGGuider(branch)
        guider.set_cfg(2.)
        cond = [[torch.randn(1, 2, 16) * .1, {}]]
        guider.set_conds(cond, cond)
        noise = torch.randn(1, 2, 4, 6)
        with torch.set_grad_enabled(False):
            output = guider.sample(noise, torch.zeros_like(noise), fixtures.comfy.samplers.sampler_object("euler"),
                                   torch.tensor([1., .5, 0.]), disable_pbar=True, seed=42)
        self.assertEqual(output.shape, noise.shape)
        self.assertTrue(torch.isfinite(output).all())
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].closed)
        self.assertFalse(runs[0].entries)
        self.assertIsNone(runs[0].free_memory)
        self.assertGreater(runs[0].stats["adapter_hits"], 0)
        self.assertIsNone(context.runtime.get())
        self.assertIsNone(context.current.get())
        branch.unpatch_model()

    def test_cached_file_never_reuses_a_different_models_mapping(self):
        torch.save(self.weights, self.file)
        with patch.object(fixtures.folder_paths, "get_filename_list", return_value=["test.pt"]), \
             patch.object(fixtures.folder_paths, "get_full_path_or_raise", return_value=str(self.file)), \
             patch.object(ml, "save_asset"), patch.object(ml, "read_adapter_file", wraps=ml.read_adapter_file) as parser:
            node = ml.WepeNerdLoadLoraMasked()
            node.load(self.model, "test.pt", .5, mask=torch.ones(4, 6))
            changed = self.model.clone()
            changed.add_object_patch("diffusion_model.first", torch.nn.Linear(8, 33))
            with self.assertRaisesRegex(ValueError, "does not match"):
                node.load(changed, "test.pt", .5, mask=torch.ones(4, 6))
            self.assertEqual(parser.call_count, 1)


if __name__ == "__main__":
    unittest.main()
