"""Model-percent scheduling through native sigma, conditioning and Krea2 wrappers."""

from dataclasses import replace
import unittest
from unittest.mock import patch

import torch
import torch.nn.functional as F

import test_masked_lora as fixtures
from test_masked_lora_runtime import layout
import comfy.model_sampling
from comfy_extras.nodes_custom_sampler import Guider_Basic

ml, pe = fixtures.ml, fixtures.pe


def descriptor(start=0., end=1., mode="Both"):
    return replace(fixtures.region_spec(torch.ones(4, 6)).descriptor,
                   start_percent=start, end_percent=end, apply_to=mode)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.sampling = comfy.model_sampling.ModelSamplingFlux()
        self.sampling.set_parameters(shift=1.7)

    def rows(self, d, values, labels=(0,), batch=1, verified=True):
        return ml.schedule_rows(d, layout(batch, labels), values, self.sampling, verified)

    def test_validation_including_noop_ranges(self):
        for start, end in ((-.1, 1), (0, 1.1), (float("nan"), 1), (0, float("inf")), (.8, .2), (None, 1), (False, 1), (0, True)):
            with self.assertRaises(ValueError):
                ml.validate_schedule(start, end, "Both")
        with self.assertRaisesRegex(ValueError, "apply_to"):
            ml.validate_schedule(0, 1, "Negative only")
        ml.validate_schedule(.4, .4, "Both")
        self.assertEqual(self.rows(descriptor(.4, .4), None), (False,))

    def test_full_range_is_explicit_fast_path_including_custom_excursions(self):
        with patch.object(self.sampling, "percent_to_sigma", side_effect=AssertionError("full range converted")):
            self.assertEqual(self.rows(descriptor(), (float("inf"), -1, 1e12), batch=3), (True,) * 3)
            self.assertEqual(self.rows(descriptor(), None), (True,))

    def test_endpoints_and_reordered_solver_calls_have_no_counter(self):
        d = descriptor(.2, .7)
        low, high = self.sampling.percent_to_sigma(.7), self.sampling.percent_to_sigma(.2)
        for sigma, expected in ((high, True), (low, True), (low-.001, False), (high+.001, False),
                                (high, True), (low, True), (low-.001, False), ((low+high)/2, True)):
            self.assertEqual(self.rows(d, (sigma,)), (expected,))
        # float32 representation at exact endpoints is covered by the documented tolerance.
        for sigma in (high, low):
            self.assertEqual(self.rows(d, (torch.tensor(sigma).item(),)), (True,))

    def test_partial_denoise_uses_model_percent_and_live_shift(self):
        d = descriptor(0, .5)
        self.assertEqual(self.rows(d, (.3,)), (False,))
        self.sampling.set_parameters(shift=-1.)
        self.assertEqual(self.rows(d, (.3,)), (True,))
        self.sampling = comfy.model_sampling.ModelSamplingDiscreteFlow()
        self.sampling.set_parameters(shift=3.)
        self.assertEqual(self.rows(d, (.5,)), (False,))
        self.sampling.set_parameters(shift=1.)
        self.assertEqual(self.rows(d, (.5,)), (True,))

    def test_positive_chunk_permutations_split_calls_cfg_one_and_repeated_labels(self):
        d = descriptor(mode="Positive only")
        for labels in ((0, 1), (1, 0), (0, 0, 1), (0,), (1,)):
            self.assertEqual(self.rows(d, None, labels, 2), tuple(label == 0 for label in labels for _ in range(2)))
        with self.assertRaisesRegex(ValueError, "verified"):
            self.rows(d, None, verified=False)
        with self.assertRaisesRegex(ValueError, "verified"):
            self.rows(d, None, (0, 2))

    def test_distinct_batch_sigmas_expand_with_mask_rows(self):
        d = descriptor(.2, .7, "Positive only")
        high = self.sampling.percent_to_sigma(.2)
        call = layout(2, (1, 0, 0))
        self.assertEqual(call.mask_rows(2), (0, 1, 0, 1, 0, 1))
        self.assertEqual(self.rows(d, (0, high)*3, (1, 0, 0), 2), (False, False, False, True, False, True))
        with self.assertRaisesRegex(ValueError, "every activation row"):
            self.rows(d, (0, high), (1, 0), 2)


class NativeScheduleTests(unittest.TestCase):
    setUp = fixtures.NativeIntegrationTests.setUp
    load = fixtures.NativeIntegrationTests.load

    def prepare(self, model, guider_type=fixtures.comfy.samplers.CFGGuider):
        model.patch_model(load_weights=False)
        self.addCleanup(lambda: model.unpatch_model(unpatch_weights=False))
        model.model.current_patcher = model
        fixtures.comfy.sampler_helpers.prepare_model_patcher(model, {}, model.model_options)
        context = model.object_patches["diffusion_model.first.forward"].context
        guider = guider_type(model)
        return context, guider

    def run_outer(self, context, guider, function):
        return pe.WrapperExecutor.new_class_executor(function, guider, [context.outer_sample]).execute()

    def forward(self, model, x, sigma, labels=(0, 1), missing=False, failure=False):
        context = model.object_patches["diffusion_model.first.forward"].context
        options = dict(model.model_options["transformer_options"])
        options["wrappers"] = pe.copy_nested_dicts(model.wrappers)
        if not missing:
            options["cond_or_uncond"] = labels
        def apply(*unused):
            if failure:
                raise RuntimeError("test failure")
            return model.model.apply_model(x.repeat(len(labels), 1, 1, 1), sigma.repeat(len(labels)),
                c_crossattn=torch.zeros(len(labels)*x.shape[0], 2, 16), transformer_options=options)
        return context.conditioning_batch(apply, model.model, [[], []], x, sigma, model.model_options)

    def test_raw_sigma_before_timestep_and_process_timestep_with_distinct_rows(self):
        model, _ = self.load(mask=torch.ones(4, 6), start_percent=.2, end_percent=.7, apply_to="Positive only")
        context, guider = self.prepare(model)
        observed = []
        def record(executor, x, timestep, *args, **kwargs):
            gate = next(iter(context.gates.get().values()))
            observed.append((timestep.clone(), gate.clone(), context.raw_sigma.get()[0]))
            return executor(x, timestep, *args, **kwargs)
        model.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "record", record)
        high = model.model.model_sampling.percent_to_sigma(.2)
        sigma = torch.tensor([0., high])
        with patch.object(model.model.model_sampling, "timestep", side_effect=lambda s: s * 100 + 5), \
             patch.object(model.model, "process_timestep", side_effect=lambda t, **kwargs: t + 11):
            self.run_outer(context, guider, lambda: self.forward(model, torch.zeros(2, 2, 4, 6), sigma, (1, 0, 0)))
        t, gate, raw = observed[0]
        torch.testing.assert_close(t, sigma.repeat(3)*100 + 16)
        self.assertEqual(raw, tuple(sigma.tolist())*3)
        self.assertEqual(gate.tolist(), [3, 5])
        self.assertIsNone(context.raw_sigma.get())
        self.assertIsNone(context.gates.get())
        self.assertFalse(context.positive_verified.get())

    def test_native_cfg_sampler_and_basic_guider(self):
        for guider_type in (fixtures.comfy.samplers.CFGGuider, Guider_Basic):
            model, _ = self.load(mask=torch.ones(4, 6), apply_to="Positive only")
            context, guider = self.prepare(model, guider_type)
            cond = {"model_conds": {"c_crossattn": fixtures.comfy.conds.CONDRegular(torch.randn(2, 2, 16)*.1)}, "uuid": "test"}
            guider.inner_model = model.model
            guider.conds = {"positive": [cond], "negative": [cond]} if guider_type is not Guider_Basic else {"positive": [cond]}
            seen = []
            def record(executor, *args, **kwargs):
                seen.extend(context.current.get().chunk_labels)
                return executor(*args, **kwargs)
            model.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "record", record)
            fixtures.comfy.sampler_helpers.prepare_model_patcher(model, {}, model.model_options)
            for cfg in (1., 3.):
                guider.set_cfg(cfg)
                x = torch.randn(2, 2, 4, 6)*.1
                result = self.run_outer(context, guider, lambda: guider.predict_noise(x, torch.ones(2)*.5, model.model_options))
                self.assertEqual(result.shape, x.shape)
                self.assertTrue(torch.isfinite(result).all())
                self.assertIn(0, seen)
                if cfg == 1. or guider_type is Guider_Basic:
                    self.assertNotIn(1, seen)
                else:
                    self.assertIn(1, seen)
                seen.clear()
            model.unpatch_model(unpatch_weights=False)

    def test_unknown_guider_missing_metadata_and_custom_evaluator_fail(self):
        model, _ = self.load(mask=torch.ones(4, 6), apply_to="Positive only")
        context, guider = self.prepare(model)
        class UnknownGuider(fixtures.comfy.samplers.CFGGuider):
            pass
        with self.assertRaisesRegex(ValueError, "Custom guiders"):
            self.run_outer(context, UnknownGuider(model), lambda: None)
        with self.assertRaisesRegex(ValueError, "chunk metadata"):
            self.run_outer(context, guider, lambda: self.forward(model, torch.zeros(1, 2, 4, 6), torch.ones(1), missing=True))
        guider.model_options["sampler_calc_cond_batch_function"] = lambda args: None
        with self.assertRaisesRegex(ValueError, "Custom guiders"):
            self.run_outer(context, guider, lambda: None)
        del guider.model_options["sampler_calc_cond_batch_function"]
        for kind in (pe.WrappersMP.PREDICT_NOISE, pe.WrappersMP.CALC_COND_BATCH):
            pe.add_wrapper_with_key(kind, "custom", lambda executor, *args: executor(*args), guider.model_options, is_model_options=True)
            with self.assertRaisesRegex(ValueError, "custom guider/conditioning wrappers"):
                self.run_outer(context, guider, lambda: None)
            del guider.model_options["transformer_options"]["wrappers"][kind]["custom"]

    def test_full_range_output_matches_ungated_dispatch_for_black_full_soft_masks(self):
        for mask in (torch.zeros(4, 6), torch.ones(4, 6), torch.linspace(0, 1, 6).repeat(4, 1)):
            model, _ = self.load(mask=mask)
            if not torch.any(mask):
                self.assertFalse(model.object_patches)
                continue
            context, guider = self.prepare(model)
            x = torch.randn(1, 2, 4, 6)*.1
            sigma = torch.tensor([1.2]) # Beyond the model's normal sigma start.
            default = self.run_outer(context, guider, lambda: self.forward(model, x, sigma))
            # Phase 2 behavior: no descriptor gates and no APPLY_MODEL wrapper.
            self.assertEqual(context.descriptors, ())
            model.remove_wrappers_with_key(pe.WrappersMP.APPLY_MODEL, ml.WRAPPER_KEY)
            reference = self.run_outer(context, guider, lambda: self.forward(model, x, sigma))
            torch.testing.assert_close(default, reference, atol=0, rtol=0)
            model.unpatch_model(unpatch_weights=False)

    def test_overlapping_schedules_sum_both_independent_contributions(self):
        first, _ = self.load(mask=torch.ones(4, 6), strength=-.3, end_percent=.8)
        model, _ = self.load(source=first, mask=torch.full((4, 6), .4), strength=.7,
                             start_percent=.2, apply_to="Positive only")
        context, guider = self.prepare(model)
        layer = model.get_model_object("diffusion_model.first")
        adapter = fixtures.comfy.lora.load_lora(self.weights, fixtures.comfy.lora.model_lora_keys_unet(model.model, {}), log_missing=False)["diffusion_model.first.weight"]
        x = torch.randn(2, 6, 8)*.1
        def check(executor, *args, **kwargs):
            actual = layer(x)
            for row, scale in enumerate((-.3 + .7*.4, -.3)):
                expected = F.linear(x[row], fixtures.reference_weight(layer, [(adapter, scale)]), layer.bias)
                torch.testing.assert_close(actual[row], expected, atol=1e-6, rtol=1e-5)
            return torch.zeros(2, 2, 4, 6)
        model.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "check", check)
        sigma = model.model.model_sampling.percent_to_sigma(.5)
        self.run_outer(context, guider, lambda: self.forward(model, torch.zeros(1, 2, 4, 6), torch.tensor([sigma])))

    def test_cleanup_after_inner_error_and_nonfinite_sigma(self):
        model, _ = self.load(mask=torch.ones(4, 6), end_percent=.5)
        context, guider = self.prepare(model)
        runs = []
        def fail(executor, *args, **kwargs):
            runs.append(context.runtime.get())
            raise RuntimeError("after gate")
        model.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "failure", fail)
        with self.assertRaisesRegex(RuntimeError, "after gate"):
            self.run_outer(context, guider, lambda: self.forward(model, torch.zeros(1, 2, 4, 6), torch.ones(1)))
        with self.assertRaisesRegex(ValueError, "finite"):
            self.run_outer(context, guider, lambda: self.forward(model, torch.zeros(1, 2, 4, 6), torch.tensor([float("nan")])))
        self.assertTrue(runs[0].closed)
        for variable in (context.runtime, context.raw_sigma, context.gates, context.current, context.sampling):
            self.assertIsNone(variable.get())

    def test_chained_windows_signed_strength_sum_and_inactive_work_bypass(self):
        a, _ = self.load(mask=torch.full((4, 6), .7), strength=-.4, end_percent=.4)
        model, _ = self.load(source=a, mask=torch.full((4, 6), .3), strength=.6,
                             start_percent=.6, apply_to="Positive only")
        context, guider = self.prepare(model)
        layer = model.get_model_object("diffusion_model.first")
        x = torch.randn(4, 6, 8)*.1
        weights = fixtures.comfy.lora.load_lora(self.weights, fixtures.comfy.lora.model_lora_keys_unet(model.model, {}), log_missing=False)
        adapter = weights["diffusion_model.first.weight"]
        def run():
            # Exercise actual wrappers, but use fixed activations to verify the independent weight equation.
            def check(executor, *args, **kwargs):
                sigma = context.raw_sigma.get()[0][0]
                output = layer(x)
                expected = F.linear(x, layer.weight, layer.bias)
                low = model.model.model_sampling.percent_to_sigma(.6)
                high = model.model.model_sampling.percent_to_sigma(.4)
                for row in range(4):
                    strength = -.4*.7 if sigma >= high else .6*.3 if sigma <= low and row >= 2 else 0
                    weight = fixtures.reference_weight(layer, [(adapter, strength)])
                    expected[row] = F.linear(x[row], weight, layer.bias)
                torch.testing.assert_close(output, expected, atol=1e-6, rtol=1e-5)
                return torch.zeros(4, 2, 4, 6)
            # Runs inside DIFFUSION_MODEL, so call geometry and gates are live.
            model.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "check", check)
            runtime = context.runtime.get()
            with patch.object(runtime, "adapter", wraps=runtime.adapter) as converted:
                for sigma in (0.99, 0., .99):
                    self.forward(model, torch.zeros(2, 2, 4, 6), torch.full((2,), sigma), (1, 0))
                self.assertEqual(converted.call_count, 3)
                self.assertEqual(runtime.stats["mask_projections"], 2)
                self.assertEqual(runtime.stats["adapter_hits"], 2)
                middle = model.model.model_sampling.percent_to_sigma(.5)
                with patch.object(runtime, "mask", side_effect=AssertionError("inactive mask transfer")):
                    self.forward(model, torch.zeros(2, 2, 4, 6), torch.full((2,), middle), (1, 0))
                self.assertEqual(converted.call_count, 3)
        self.run_outer(context, guider, run)

    def test_schedule_identity_old_api_defaults_equal_noop_and_sampling_replacement(self):
        old, _ = self.load(mask=torch.ones(4, 6))
        default = old.get_attachment("wepenerd_masked_loras")[0]
        self.assertEqual((default.start_percent, default.end_percent, default.apply_to), (0, 1, "Both"))
        for kwargs in ({"start_percent": .2}, {"end_percent": .8}, {"apply_to": "Positive only"}):
            changed, _ = self.load(mask=torch.ones(4, 6), **kwargs)
            self.assertFalse(old.clone_has_same_weights(changed))
        with patch.object(ml.FILE_CACHE, "load", side_effect=AssertionError("disabled read adapter")):
            noop, mask = self.load(mask=torch.ones(5, 7), start_percent=.4, end_percent=.4)
        self.assertFalse(noop.object_patches)
        self.assertEqual(mask.shape, (1, 5, 7))
        model, _ = self.load(mask=torch.ones(4, 6), end_percent=.5)
        context, guider = self.prepare(model)
        seen = []
        def record(executor, *args, **kwargs):
            seen.append(next(iter(context.gates.get().values())))
            return executor(*args, **kwargs)
        model.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, "record", record)
        def run():
            sampling_type = type(model.model.model_sampling)
            for shift in (3., -3., 3.):
                model.model.model_sampling = sampling_type()
                model.model.model_sampling.set_parameters(shift=shift)
                self.forward(model, torch.zeros(1, 2, 4, 6), torch.tensor([.5]))
        self.run_outer(context, guider, run)
        self.assertEqual(seen, [False, True, False])


if __name__ == "__main__":
    unittest.main()
