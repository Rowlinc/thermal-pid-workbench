import math
import io
import unittest
from unittest.mock import patch

from core.offline_evaluation import evaluate_step
from offline_compare import FOPDTPlant, ParallelController, compare, heating_probe
from system_id import (ideal_to_parallel, parallel_to_ideal, tuning_candidates,
                       system_identify, extract_initial_pid, normalize_time_axis, read_from_file)
from pid_safety import apply_pid_guardrails, get_pid_limits
from core.config import CONFIG
from core.tuning_session import create_tuning_session, RoundEvaluation, finalize_decision


class OfflineTuningTests(unittest.TestCase):
    def test_formula_and_conversion(self):
        table = tuning_candidates(2, 100, 5, 5)
        self.assertAlmostEqual(table["ZN_PID"]["Kp"], 12)
        self.assertAlmostEqual(table["ZN_PID"]["Ki"], 1.2)
        self.assertAlmostEqual(table["ZN_PID"]["Kd"], 30)
        self.assertEqual(table["SIMC_PI"]["Ti"], 40)
        self.assertEqual(set(table), {"ZN_PID", "ZN_PI", "SIMC_PI"})
        self.assertEqual(table["SIMC_PI"]["Kp"], 5)
        self.assertEqual(table["SIMC_PI"]["Ki"], 0.125)
        gains = ideal_to_parallel(12, 10, 2.5)
        self.assertEqual(gains["Ki"], 1.2)
        self.assertEqual(gains["Kd"], 30)
        self.assertEqual(parallel_to_ideal(12, 1.2, 30),
                         {"controller_form": "ideal", "Kp":12, "Ti":10, "Td":2.5})
        negative = tuning_candidates(-2, 100, 5, 5)
        self.assertEqual(negative["SIMC_PI"]["Kp"], -5)
        self.assertEqual(negative["SIMC_PI"]["Ki"], -0.125)
        for bad in (0, -1, math.nan, math.inf):
            self.assertIn("error", tuning_candidates(2, 100, 5, bad)["SIMC_PI"])
        zero_delay = tuning_candidates(2, 100, 0)
        self.assertIn("error", zero_delay["ZN_PID"])
        self.assertNotIn("error", zero_delay["SIMC_PI"])

    def test_identification_known_signed_step_and_units(self):
        times = list(range(1601))
        for K, delta_u in ((0.8, 10), (-0.8, 10), (0.8, -10)):
            temperatures = [70 + K*delta_u*(1-math.exp(-max(0, t-30)/100)) for t in times]
            inputs = [0 if t < 10 else delta_u for t in times]
            result = system_identify(times, temperatures, inputs)
            self.assertNotIn("error", result)
            self.assertAlmostEqual(result["model"]["K"], K, places=4)
            self.assertAlmostEqual(result["model"]["tau"], 100, delta=0.1)
            self.assertAlmostEqual(result["model"]["theta"], 20, delta=0.1)
            in_ms = system_identify([t*1000 for t in times], temperatures, inputs, time_unit="ms")
            self.assertEqual(result["model"], in_ms["model"])
            self.assertEqual(extract_initial_pid(result, "PI", "SIMC")["i"], result["tunings"]["SIMC_PI"]["Ki"])
        self.assertEqual(normalize_time_axis([100000, 100020, 100040]), [0, 20, 40])

    def test_reject_bad_data(self):
        for times, temps, inputs in (([0]*5, [1]*5, None),
                                     (list(range(5)), [1]*4, None),
                                     (list(range(5)), [1,2,3,4,5], [10]*5),
                                     (list(range(5)), [1,2,3,4,5], [0,10,0,10,10]),
                                     (list(range(5)), [1,2,math.nan,4,5], None)):
            self.assertIn("error", system_identify(times, temps, inputs))

    def test_bad_csv_row_does_not_misalign_arrays(self):
        data = "timestamp,input,pwm\n0,20,0\n1,broken,10\n2,21,10\n3,22,10\n4,23,10\n5,24,10\n6,25,10\n"
        with patch("system_id.os.path.exists", return_value=True), patch("builtins.open", return_value=io.StringIO(data)):
            result = read_from_file("historical.csv", "s")
            self.assertNotIn("error", result)

    def test_fractional_delay_matches_analytic_step(self):
        plant = FOPDTPlant(2, 10, 2.5, 1, ambient=0)
        for n in range(1, 21):
            plant.pwm = 3
            plant.update()
            expected = 6*(1-math.exp(-max(0,n-2.5)/10))
            self.assertAlmostEqual(plant.temp, expected, places=12)

    def test_metrics_hand_calculation_and_unsettled(self):
        rows = [{"time_s": t, "input": y, "pwm": u} for t,y,u in ((0,0,0),(1,8,2),(2,12,1),(3,10,1),(4,10,1))]
        metrics = evaluate_step(rows, 0, 10)
        self.assertEqual(metrics["overshoot_pct"], 20)
        self.assertEqual(metrics["iae_c_s"], 9)
        self.assertEqual(metrics["output_total_variation"], 3)
        self.assertEqual(metrics["settling_time_s"], 3)
        self.assertEqual(metrics["steady_state_error_c"], 0)
        self.assertIsNone(evaluate_step(rows[:3],0,10)["settling_time_s"])
        self.assertIsNone(evaluate_step(rows[:4],0,10)["settling_time_s"])

    def test_integrator_stops_winding_up(self):
        controller = ParallelController({"Kp":10,"Ki":2,"Kd":0}, 1, 20)
        for _ in range(100):
            self.assertEqual(controller.compute(100,20),255)
        self.assertEqual(controller.integral,0)

    def test_native_probe_and_fair_repeatable_comparison(self):
        result = heating_probe()
        self.assertNotIn("error", result)
        self.assertAlmostEqual(result["model"]["K"], 300/255/1.1, places=4)
        table = tuning_candidates(.8,300,20,20)
        factory = lambda: FOPDTPlant(.8,300,20,1)
        a, _ = compare(factory,table,80,1,2400)
        b, _ = compare(factory,table,80,1,2400)
        self.assertEqual(a,b)
        self.assertLess(a["SIMC_PI"]["metrics"]["overshoot_pct"],a["ZN_PID"]["metrics"]["overshoot_pct"])

    def test_all_sources_share_guardrails_before_plant_creation(self):
        proposal = {"Kp":10000.0, "Ki":1000.0, "Kd":1000.0}
        candidates = {name:dict(proposal) for name in ("ZN_PID", "SIMC_PI", "LLM")}
        events=[]
        def checked(current, candidate, limits=None):
            events.append("check")
            return apply_pid_guardrails(current,candidate,limits)
        def factory():
            self.assertEqual(events[-1],"check")
            events.append("plant")
            return FOPDTPlant(.8,300,20,1)
        with patch("offline_compare.apply_pid_guardrails", side_effect=checked):
            results,traces=compare(factory,candidates,80,1,30)
        for result in results.values():
            self.assertEqual(result["requested_gains"],proposal)
            self.assertEqual([result["gains"][k] for k in ("Kp","Ki","Kd")],[3,.4,.2])
            self.assertEqual(result["safety_status"],"adjusted")
            self.assertTrue(result["guardrail_notes"])
        self.assertEqual(traces["ZN_PID"],traces["SIMC_PI"])
        self.assertEqual(traces["SIMC_PI"],traces["LLM"])

    def test_nonfinite_offline_suggestion_never_constructs_plant(self):
        factory=unittest.mock.Mock()
        results,traces=compare(factory,{"LLM":{"Kp":math.nan,"Ki":1,"Kd":0}},80,1,30)
        factory.assert_not_called()
        self.assertEqual(results["LLM"]["safety_status"],"rejected")
        self.assertEqual(traces,{})

    def test_llm_finalize_and_offline_suggestion_use_same_policy(self):
        baseline={"p":1.0,"i":.1,"d":.05}
        candidate={"p":10000.0,"i":1000.0,"d":1000.0,"status":"TUNING"}
        state=create_tuning_session(initial_pid=baseline,setpoint=80)
        evaluation=RoundEvaluation(round_index=1,metrics={},current_pid=baseline,stable_rounds=0)
        decision=finalize_decision(state,evaluation,candidate,limits=get_pid_limits("python_sim"))
        results,_=compare(lambda:FOPDTPlant(.8,300,20,1),
                          {"LLM":{"Kp":candidate["p"],"Ki":candidate["i"],"Kd":candidate["d"]}},80,1,30)
        applied=results["LLM"]["gains"]
        self.assertEqual(decision.safe_pid,{"p":applied["Kp"],"i":applied["Ki"],"d":applied["Kd"]})
        self.assertEqual(decision.guardrail_notes,results["LLM"]["guardrail_notes"])

    def test_invalid_llm_gains_are_held_and_explained(self):
        baseline={"p":1.0,"i":.1,"d":.05}
        result,notes=apply_pid_guardrails(baseline,{"p":math.nan,"i":"broken","d":math.inf})
        self.assertEqual(result,baseline)
        self.assertEqual(len(notes),3)

    def test_configured_limit_and_global_ratio_apply_to_every_source(self):
        with patch.dict(CONFIG,{"PID_MAX_INCREASE_RATIO":2.0}):
            results,_=compare(lambda:FOPDTPlant(.8,300,20,1),
                              {"SIMC_PI":{"Kp":30,"Ki":2,"Kd":2}},80,1,30)
        gains=results["SIMC_PI"]["gains"]
        self.assertEqual([gains[k] for k in ("Kp","Ki","Kd")],[2,.2,.1])


if __name__ == "__main__":
    unittest.main()
