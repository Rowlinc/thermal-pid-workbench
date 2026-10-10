import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.config import CONFIG
from core.warm_start import choose_candidate, probe_heating, select_initial_pid, original_zn_initialization
from offline_compare import FOPDTPlant
from pid_safety import score_metrics
from sim.model import HeatingSimulator
from tuning_pipeline import main, run_episode, RecordedTuner, MockTuner


def candidate(iae=10,overshoot=0,tail=.01,settling=5,tv=5):
    return {"gains":{"Kp":2.,"Ki":.1,"Kd":0.},"metrics":{
        "iae_c_s":iae,"overshoot_pct":overshoot,"steady_state_error_c":tail,
        "settling_time_s":settling,"output_total_variation":tv}}


class PipelineTests(unittest.TestCase):
    def test_feasibility_beats_small_iae(self):
        results={"ZN_PID":candidate(iae=1,overshoot=15),"SIMC_PI":candidate(iae=100)}
        choice=choose_candidate(results,{"p":1,"i":.1,"d":.05})
        self.assertEqual(choice["selected_method"],"SIMC_PI")
        self.assertFalse(results["ZN_PID"]["selection"]["eligible"])

    def test_iae_then_output_variation_is_deterministic(self):
        results={"ZN_PID":candidate(iae=100,tv=20),"SIMC_PI":candidate(iae=100,tv=10)}
        self.assertEqual(choose_candidate(results,{"p":1,"i":.1,"d":0})["selected_method"],"SIMC_PI")
        results["ZN_PID"]["metrics"]["iae_c_s"]=99
        self.assertEqual(choose_candidate(results,{"p":1,"i":.1,"d":0})["selected_method"],"ZN_PID")

    def test_no_eligible_candidate_holds_current(self):
        baseline={"p":1,"i":.1,"d":.05}
        choice=choose_candidate({"ZN_PID":candidate(settling=None),"SIMC_PI":candidate(tail=5)},baseline)
        self.assertEqual(choice["selected_pid"],baseline)
        self.assertEqual(choice["status"],"no_eligible_candidate")

    def test_nonfinite_candidate_cannot_win(self):
        choice=choose_candidate({"ZN_PID":candidate(iae=math.nan),"SIMC_PI":candidate()}, {"p":1,"i":.1,"d":0})
        self.assertEqual(choice["selected_method"],"SIMC_PI")

    def test_probe_does_not_mutate_running_simulator(self):
        sim=HeatingSimulator()
        sim.temp=80
        original=(sim.temp,sim.pwm,sim.timestamp,sim.step_count)
        result=probe_heating(sim)
        self.assertNotIn("error",result)
        self.assertEqual(original,(sim.temp,sim.pwm,sim.timestamp,sim.step_count))

    def test_select_then_handoff_to_original_engine(self):
        identified={"model":{"K":.8,"tau":300.,"theta":20.}}
        factory=lambda:FOPDTPlant(.8,300,20,1)
        init,_=select_initial_pid(identified,factory,{"p":2.,"i":.1,"d":.05},80,1,2400)
        self.assertEqual(init["selection"]["selected_method"],"SIMC_PI")
        seen=[]
        def tuner_factory(env):
            seen.append(env.get_current_pid()[0])
            return RecordedTuner(MockTuner(env),"mock")
        episode,rows=run_episode(factory,init["selection"]["selected_pid"],80,1,"thermal",tuner_factory,rounds=2,samples=50)
        self.assertEqual(seen,[init["selection"]["selected_pid"]])
        self.assertGreater(len(episode["tuner_calls"]),0)
        self.assertGreater(len(rows),50)
        self.assertTrue(episode["apply_audit"])

    def test_changed_done_proposal_is_applied_and_verified_not_prematurely_accepted(self):
        class DoneTuner:
            def analyze(self,*a,**k):
                return {"p":10000.,"i":1000.,"d":1000.,"status":"DONE"}
        factory=lambda:FOPDTPlant(.8,300,20,1)
        episode,_=run_episode(factory,{"p":1.,"i":.1,"d":.05},80,1,"thermal",
                              lambda env:RecordedTuner(DoneTuner(),"mock"),rounds=2,samples=10)
        self.assertEqual(len(episode["tuner_calls"]),3)
        self.assertEqual(episode["engine"]["completed_reason"],"guardrail_retry_limit")
        self.assertEqual(episode["apply_audit"],[])
        self.assertEqual(episode["actual_final_pid"],{"p":1.,"i":.1,"d":.05})

    def test_api_failure_keeps_original_fallback_mechanism(self):
        class Failed:
            def analyze(self,*a,**k): return None
        episode,_=run_episode(lambda:FOPDTPlant(.8,300,20,1),{"p":1.,"i":.1,"d":.05},80,1,"thermal",
                              lambda env:RecordedTuner(Failed(),"mock"),rounds=1,samples=10)
        self.assertEqual(episode["engine"]["fallback_count"],1)
        self.assertTrue(episode["apply_audit"])

    def test_original_initialization_snapshot_is_used(self):
        result=original_zn_initialization({"p":1.,"i":.1,"d":.05})
        self.assertEqual(result["status"],"original_zn")
        self.assertIsNotNone(result["requested_pid"])
        self.assertGreater(result["identification"]["model"]["K"],0)
        self.assertNotIn("tunings",result["identification"])

    def test_zero_error_is_not_scored_as_missing(self):
        good={"avg_error":0.,"steady_state_error":0.,"overshoot":0.,"status":"STABLE"}
        self.assertEqual(score_metrics(good),0.)

    def test_native_selection_uses_native_controller_without_target_switch(self):
        sim=HeatingSimulator(dynamic_setpoint=False,setpoint=100)
        sim.noise_level=0
        for _ in range(100): sim.compute_pid(); sim.update()
        self.assertEqual(sim.setpoint,100)
        factory=lambda:HeatingSimulator(dynamic_setpoint=False,setpoint=100)
        init,_=select_initial_pid(probe_heating(),factory,{"p":1.,"i":.1,"d":.05},100,.2,200,controller_kind="native")
        self.assertEqual(init["settings"]["controller_kind"],"native")

    def test_complete_mock_comparison_outputs_paired_arms(self):
        with tempfile.TemporaryDirectory() as folder:
            report=main(["--tuner","mock","--compare-original","--rounds","2","--out",folder])
            self.assertEqual(set(report["arms"]),{"original_zn","corrected_zn","selected"})
            self.assertEqual(report["tuner"],"mock")
            self.assertTrue((Path(folder)/"report.html").exists())
            for arm in report["arms"].values():
                self.assertIn("best_result",arm["episode"]["engine"])
                self.assertEqual(arm["validation"]["gains"]["Kp"],arm["validation_pid"]["p"])
                self.assertIn("INITIAL_PID",arm["final_candidate_results"])
                self.assertIn("ENGINE_FINAL",arm["final_candidate_results"])

    def test_real_llm_adapter_is_called_with_mocked_transport(self):
        # Exercise actual LLMTuner prompt/parser/engine integration without network or billing.
        from llm.client import LLMTuner
        with tempfile.TemporaryDirectory() as folder:
            cfg=Path(folder)/"config.json"
            cfg.write_text(json.dumps({"LLM_API_KEY":"test-key-not-real","LLM_API_BASE_URL":"https://example.invalid/v1",
                                       "LLM_MODEL_NAME":"test-model","LLM_PROVIDER":"openai"}),encoding="utf-8")
            saved=dict(CONFIG)
            try:
                with patch.object(LLMTuner,"_execute_request",return_value='{"p":2.0,"i":0.2,"d":0.0,"status":"TUNING"}') as transport:
                    report=main(["--tuner","llm","--config",str(cfg),"--rounds","2","--out",str(Path(folder)/"result")])
                self.assertGreater(transport.call_count,0)
                self.assertTrue(report["arms"]["selected"]["episode"]["tuner_calls"])
                self.assertNotIn("test-key-not-real",(Path(folder)/"result"/"summary.json").read_text(encoding="utf-8"))
            finally:
                CONFIG.clear(); CONFIG.update(saved)

    def test_missing_real_credentials_fail_before_llm_call(self):
        with patch.dict(CONFIG,{"LLM_API_KEY":"your-api-key-here"}), patch.dict("os.environ",{},clear=True):
            with self.assertRaises(SystemExit):
                main(["--tuner","llm","--config","nonexistent-config.json"])

    def test_unconfirmed_connection_never_constructs_llm_client(self):
        import simulator
        with patch.dict(simulator.CONFIG,{"LLM_API_BASE_URL":"","LLM_MODEL_NAME":""}), patch.object(simulator,"LLMTuner") as client:
            with self.assertRaises(ValueError):
                simulator._run_tuning_loop(HeatingSimulator(),100,"Python",warm_start=False,emit_console=False)
            client.assert_not_called()


if __name__=="__main__": unittest.main()
