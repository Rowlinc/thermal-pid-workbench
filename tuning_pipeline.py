#!/usr/bin/env python3
"""Complete local thermal workflow: identification -> checked trials -> selection -> LLM.

--tuner mock never calls an API. --tuner llm explicitly enables the original LLMTuner.
"""
import argparse
from contextlib import contextmanager
import csv
import html
import json
import math
import os
from pathlib import Path
import random

from core.buffer import AdvancedDataBuffer
from core.config import CONFIG, ensure_utf8_console
from core.offline_evaluation import evaluate_step
from core.tuning_engine import run_tuning_engine
from core.warm_start import probe_heating, select_initial_pid, original_zn_initialization, choose_candidate
from offline_compare import FOPDTPlant, compare, DEFAULT_CURRENT_PID, checked_gains
from pid_safety import apply_pid_guardrails, get_pid_limits
from sim.model import HeatingSimulator, CONTROL_INTERVAL
from sim.thermal_env import ThermalTuningEnv
from system_id import first_order_model, read_from_file


@contextmanager
def experiment_config(rounds, samples):
    saved=dict(CONFIG)
    CONFIG.update({"MAX_TUNING_ROUNDS":rounds,"BUFFER_SIZE":samples,"CSV_EXPORT_PATH":""})
    try: yield
    finally:
        CONFIG.clear()
        CONFIG.update(saved)


class MockTuner:
    """Deterministic test double for wiring tests; not an LLM or evidence of LLM quality."""
    def __init__(self, env): self.env=env
    def analyze(self, *_args, **_kwargs):
        p=self.env.get_current_pid()[0]
        m=self.env.last_metrics
        if m.get("overshoot",0)>5 or m.get("status")=="OSCILLATING":
            p={"p":p["p"]*.85,"i":p["i"]*.9,"d":p["d"]*1.15}
        elif m.get("steady_state_error",0)>.3:
            p={"p":p["p"]*1.1,"i":p["i"]*1.15,"d":p["d"]}
        return {**p,"status":"TUNING","tuning_action":"MOCK_ADJUST",
                "analysis_summary":"Deterministic test double; no LLM request was made."}


class RecordedTuner:
    def __init__(self, tuner, kind):
        self.tuner=tuner
        self.kind=kind
        self.calls=[]
    def analyze(self,*args,**kwargs):
        if self.kind=="llm": print(f"  [LLM] Request {len(self.calls)+1}...",flush=True)
        result=self.tuner.analyze(*args,**kwargs)
        self.calls.append({"call":len(self.calls)+1,"response_received":bool(result),
                           "proposal":{k:result.get(k) for k in ("p","i","d","status")} if result else None})
        if self.kind=="llm": print("  [LLM] Parsed response received." if result else "  [LLM] No usable response; existing fallback will handle this round.",flush=True)
        return result


def run_episode(plant_factory, initial_pid, setpoint, dt, controller_kind,
                tuner_factory, rounds=8, samples=100, prompt_context=None):
    baseline,_=apply_pid_guardrails(initial_pid,initial_pid,get_pid_limits("python_sim"))
    env=ThermalTuningEnv(plant_factory,baseline,setpoint,dt,samples,controller_kind,prompt_context)
    tuner=tuner_factory(env)
    with experiment_config(rounds,samples):
        result=run_tuning_engine(env,tuner,"python_sim",emit_console=False,require_verified_done=True)
    # Always read actual applied gains; an untested LLM proposal is never reported as applied.
    actual=env.get_current_pid()[0]
    return {"engine":result,"actual_final_pid":actual,"tuner_calls":tuner.calls,
            "apply_audit":env.apply_audit},env.rows


def write_csv(path,rows):
    with path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_dashboard(path,report,traces):
    colors={"original_zn":"#dc2626","corrected_zn":"#ea580c","selected":"#2563eb"}
    parts=['<!doctype html><html lang="zh"><meta charset="utf-8"><title>完整温控调参对照</title><style>body{font-family:system-ui;max-width:1100px;margin:30px auto;padding:20px;background:#f8fafc;color:#172033}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:9px;text-align:right}td:first-child,th:first-child{text-align:left}svg{width:100%}section{background:white;padding:22px;margin:20px 0;border-radius:10px}p{line-height:1.7}</style><h1>自动选优 → 原有调参引擎 → 独立验证</h1>']
    label="真实 LLM（请核对成功请求数）" if report["tuner"]=="llm" else "模拟建议器：流程验证，未调用 LLM"
    parts.append(f'<p><strong>{label}</strong></p>')
    selection=report["initialization"]["selection"]
    parts.append(f'<p>选中 {selection["selected_method"]}：{html.escape(selection["reason"])}。原始建议和护栏后参数见 summary.json。</p>')
    parts.append('<section><h2>初始化：Z-N 与 SIMC 参数及筛选结果</h2><table><tr><th>候选</th><th>原始 P / I / D</th><th>实际 P / I / D</th><th>超调%</th><th>IAE</th><th>符合要求</th></tr>')
    for name,item in report["initialization"]["candidate_results"].items():
        if "gains" not in item: continue
        requested=" / ".join(f'{item["requested_gains"][k]:.5g}' for k in ("Kp","Ki","Kd"))
        applied=" / ".join(f'{item["gains"][k]:.5g}' for k in ("Kp","Ki","Kd")) if item.get('gains') else '拒绝整组（未仿真）'
        if not item.get('metrics'):
            parts.append(f'<tr><td>{name}</td><td>{requested}</td><td>{applied}</td><td>—</td><td>—</td><td>{html.escape(item.get("error", "未仿真"))}</td></tr>')
            continue
        eligible="是" if item["selection"]["eligible"] else "否"
        parts.append(f'<tr><td>{name}</td><td>{requested}</td><td>{applied}</td><td>{item["metrics"]["overshoot_pct"]:.3f}</td><td>{item["metrics"]["iae_c_s"]:.3f}</td><td>{eligible}</td></tr>')
    parts.append('</table></section>')
    parts.append('<section><h2>最终参数独立重跑：相同模型、条件与时长</h2><table><tr><th>组别</th><th>超调%</th><th>IAE</th><th>末段误差°C</th><th>调节时间s</th><th>输出总变差</th><th>建议调用/回滚</th></tr>')
    for name,arm in report["arms"].items():
        m=arm["validation"]["metrics"]
        settling="未调节" if m["settling_time_s"] is None else f'{m["settling_time_s"]:.1f}'
        parts.append(f'<tr><td style="color:{colors[name]}">{name}</td><td>{m["overshoot_pct"]:.3f}</td><td>{m["iae_c_s"]:.3f}</td><td>{m["steady_state_error_c"]:.4f}</td><td>{settling}</td><td>{m["output_total_variation"]:.3f}</td><td>{len(arm["episode"]["tuner_calls"])} / {arm["episode"]["engine"]["rollback_count"]}</td></tr>')
    parts.append('</table></section>')
    parts.append('<section><h2>最终参数建议</h2><table><tr><th>组别</th><th>推荐 P / I / D</th><th>采用来源</th></tr>')
    for name,arm in report["arms"].items():
        recommended=arm["recommended_pid"]
        text="没有合格建议，保留诊断结果" if recommended is None else " / ".join(f'{recommended[k]:.6g}' for k in ("p","i","d"))
        source={"INITIAL_PID":"初始参数","ENGINE_BEST":"历史最优参数","ENGINE_FINAL":"最后实际参数","CURRENT_PID":"当前参数未满足筛选要求"}.get(arm["final_selection"]["selected_method"],arm["final_selection"]["selected_method"])
        parts.append(f'<tr><td>{name}</td><td>{text}</td><td>{source}</td></tr>')
    parts.append('</table></section>')
    for key,title in (("input","Temperature (C)"),("pwm","Control output (0-255)")):
        values=[r[key] for rows in traces.values() for r in rows]
        low,high=min(values),max(values)
        pad=max(1,(high-low)*.1)
        low,high=low-pad,high+pad
        horizon=max(rows[-1]["time_s"] for rows in traces.values())
        x=lambda t:65+900*t/horizon
        y=lambda v:285-240*(v-low)/(high-low)
        parts.append(f'<section><svg viewBox="0 0 1000 330" role="img" aria-label="{title}"><text x="65" y="25">{title}</text>')
        for i in range(6):
            value=low+(high-low)*i/5
            parts.append(f'<line x1="65" x2="965" y1="{y(value):.2f}" y2="{y(value):.2f}" stroke="#ddd"/><text x="55" y="{y(value)+4:.2f}" text-anchor="end" font-size="12">{value:.1f}</text>')
            parts.append(f'<text x="{x(horizon*i/5):.2f}" y="305" text-anchor="middle" font-size="12">{horizon*i/5:.0f}</text>')
        for name,rows in traces.items():
            sampled=rows[::max(1,len(rows)//1800)]
            if sampled[-1] != rows[-1]: sampled.append(rows[-1])
            points=" ".join(f'{x(r["time_s"]):.2f},{y(r[key]):.2f}' for r in sampled)
            parts.append(f'<polyline points="{points}" stroke="{colors[name]}" stroke-width="2" fill="none"/>')
        parts.append('<text x="500" y="325" text-anchor="middle">Time (s)</text></svg>')
        parts.append('<p>'+"　".join(f'<span style="color:{colors[n]}">{n}</span>' for n in traces)+'</p></section>')
    parts.append('<p>原版组复现原短阶跃探测和原辨识代码，后续共享同一调参引擎与控制器以隔离初始化影响。检查通过不代表过程安全；现场仍由 DCS/PLC/SIS 等保护体系负责。</p></html>')
    path.write_text("".join(parts),encoding="utf-8")


def main(argv=None):
    ensure_utf8_console()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tuner",choices=["mock","llm"],default="mock")
    parser.add_argument("--compare-original",action="store_true")
    parser.add_argument("--plant",choices=["heating","fopdt"],default="heating")
    parser.add_argument("--config",type=Path)
    parser.add_argument("--id-file",type=Path)
    parser.add_argument("--time-unit",choices=["s","ms"],default="s")
    parser.add_argument("--K",type=float,default=.8)
    parser.add_argument("--tau",type=float,default=300.0)
    parser.add_argument("--theta",type=float,default=20.0)
    parser.add_argument("--lambda",dest="lambda_",type=float)
    parser.add_argument("--rounds",type=int,default=8)
    parser.add_argument("--samples",type=int,default=100)
    parser.add_argument("--duration",type=float)
    parser.add_argument("--setpoint",type=float)
    parser.add_argument("--noise",type=float,default=0.0)
    parser.add_argument("--seed",type=int,default=0)
    parser.add_argument("--gain-scale",type=float,default=1.0)
    parser.add_argument("--disturbance-time",type=float,default=-1.0)
    parser.add_argument("--disturbance-rate",type=float,default=0.0,help="additional thermal load, C/s")
    parser.add_argument("--max-overshoot",type=float,default=5.0)
    parser.add_argument("--max-tail-error",type=float,default=.3)
    parser.add_argument("--out",type=Path,default=Path("results/pipeline"))
    args=parser.parse_args(argv)
    if args.lambda_ is not None and (not math.isfinite(args.lambda_) or args.lambda_<=0):
        parser.error("lambda must be positive and finite")
    numeric=(args.K,args.tau,args.theta,args.noise,args.gain_scale,args.disturbance_time,args.disturbance_rate)
    if not all(math.isfinite(v) for v in numeric) or args.K<=0 or args.tau<=0 or args.theta<0 or args.noise<0 or args.gain_scale<=0 or args.rounds<1 or args.samples<5:
        parser.error("finite positive K/tau/gain-scale, nonnegative theta/noise, rounds>=1, samples>=5 required")
    if args.id_file and args.plant!="fopdt": parser.error("--id-file requires --plant fopdt")
    if args.tuner=="llm":
        path=args.config or Path("config.json")
        if path.exists():
            CONFIG.update(json.loads(path.read_text(encoding="utf-8-sig")))
        elif args.config: parser.error(f"config file not found: {path}")
        for key in ("LLM_API_KEY","LLM_API_BASE_URL","LLM_MODEL_NAME","LLM_PROVIDER"):
            if os.environ.get(key): CONFIG[key]=os.environ[key]
        if not CONFIG.get("LLM_API_KEY") or CONFIG["LLM_API_KEY"]=="your-api-key-here":
            parser.error("real LLM requires LLM_API_KEY in config.json or environment; mock is only a wiring test")
        if not CONFIG.get("LLM_API_BASE_URL") or not CONFIG.get("LLM_MODEL_NAME"):
            parser.error("confirm LLM_API_BASE_URL and LLM_MODEL_NAME for this key before making requests")
    if args.plant=="heating":
        identification=probe_heating()
        dt,controller_kind=.2,"native"
        setpoint=100.0 if args.setpoint is None else args.setpoint
        horizon=200.0 if args.duration is None else args.duration
    else:
        identification=read_from_file(str(args.id_file),args.time_unit,args.lambda_) if args.id_file else {
            "model":first_order_model(args.tau,args.K,args.theta),"source":"explicit nominal model; not field identification"}
        if "error" in identification: parser.error(identification["error"])
        model=identification["model"]
        args.K,args.tau,args.theta=model["K"],model["tau"],model["theta"]
        if args.K<=0: parser.error("existing guardrail policy requires positive plant gain")
        dt,controller_kind=min(1.0,args.tau/50),"thermal"
        setpoint=80.0 if args.setpoint is None else args.setpoint
        horizon=max(200,8*args.tau) if args.duration is None else args.duration
    if not math.isfinite(setpoint) or not math.isfinite(horizon) or horizon<2*dt or setpoint<=(20 if args.plant=="heating" else 70):
        parser.error("finite heating target above ambient and duration>=2*dt required")
    def nominal_factory():
        if args.plant=="heating":
            sim=HeatingSimulator(setpoint=setpoint,random_seed=args.seed,dynamic_setpoint=False)
            sim.noise_level=0.0
            return sim
        return FOPDTPlant(args.K,args.tau,args.theta,dt)
    def actual_factory():
        plant=nominal_factory()
        rng=random.Random(args.seed)
        if args.plant=="heating":
            plant.heater_coeff*=args.gain_scale
            plant.noise_level=args.noise
        else: plant.K*=args.gain_scale
        update=plant.update
        ticks=[0]
        def scenario_update():
            update()
            ticks[0]+=1
            if args.plant=="fopdt": plant.temp+=rng.gauss(0,args.noise)
            if args.disturbance_time>=0 and ticks[0]*dt>=args.disturbance_time:
                plant.temp+=args.disturbance_rate*dt
        plant.update=scenario_update
        return plant
    rules={"max_overshoot_pct":args.max_overshoot,"max_tail_error_c":args.max_tail_error}
    try:
        initialization,trial_traces=select_initial_pid(identification,nominal_factory,DEFAULT_CURRENT_PID,
            setpoint,dt,horizon,args.lambda_,rules=rules,controller_kind=controller_kind)
    except ValueError as exc: parser.error(str(exc))
    starts={"selected":initialization["selection"]["selected_pid"]}
    reference=None
    if args.compare_original:
        if args.plant=="heating":
            reference=original_zn_initialization(DEFAULT_CURRENT_PID,buffer_size=args.samples)
        else:
            # Same long synthetic step data to isolate the old vs corrected identification.
            from reference import original_system_id as original
            times=[i*dt for i in range(round(horizon/dt)+1)]
            outputs=[0.0]+[10.0]*(len(times)-1)
            temps=[70+args.K*10*(1-math.exp(-max(0,t-dt-args.theta)/args.tau)) for t in times]
            old=original.system_identify(times,temps,outputs)
            raw=original.extract_initial_pid(old,"PID")
            safe,notes=apply_pid_guardrails(DEFAULT_CURRENT_PID,raw or DEFAULT_CURRENT_PID,get_pid_limits("python_sim"))
            reference={"identification":old,"requested_pid":raw,"selected_pid":safe,
                       "guardrail_notes":notes,"status":"original_id_on_same_synthetic_data"}
        zn=initialization["candidate_results"]["ZN_PID"]
        corrected_pid=({"p":zn["gains"]["Kp"],"i":zn["gains"]["Ki"],"d":zn["gains"]["Kd"]}
                       if zn.get("gains") else dict(DEFAULT_CURRENT_PID))
        starts={"original_zn":reference["selected_pid"],"corrected_zn":corrected_pid,**starts}
    def tuner_factory(env):
        if args.tuner=="mock": return RecordedTuner(MockTuner(env),"mock")
        from llm.client import LLMTuner
        return RecordedTuner(LLMTuner(CONFIG["LLM_API_KEY"],CONFIG["LLM_API_BASE_URL"],CONFIG["LLM_MODEL_NAME"],
                    CONFIG["LLM_PROVIDER"],emit_console=False,timeout=CONFIG.get("LLM_REQUEST_TIMEOUT",60.0)),"llm")
    arms,validation_traces={},{}
    args.out.mkdir(parents=True,exist_ok=True)
    for name,initial in starts.items():
        print(f"[{name}] initial PID={initial}; tuner={args.tuner}",flush=True)
        episode,training_rows=run_episode(actual_factory,initial,setpoint,dt,controller_kind,tuner_factory,
                                         args.rounds,args.samples,{"system_model":identification["model"],
                                         "process":"thermal temperature loop; offline only", "units":"seconds, C, output 0-255"})
        actual=episode["actual_final_pid"]
        finalists={"INITIAL_PID":initial,"ENGINE_FINAL":actual}
        if episode["engine"].get("best_result") is not None:
            finalists["ENGINE_BEST"]=episode["engine"]["best_result"]["pid"]
        final_candidates={key:{"Kp":pid["p"],"Ki":pid["i"],"Kd":pid["d"]} for key,pid in finalists.items()}
        validated,trace=compare(actual_factory,final_candidates,setpoint,dt,horizon,
                               current_pid=actual,controller_kind=controller_kind)
        final_choice=choose_candidate(validated,actual,rules)
        winner=final_choice["selected_method"] if final_choice["status"]=="selected" else "ENGINE_FINAL"
        validation_pid={"p":validated[winner]["gains"]["Kp"],"i":validated[winner]["gains"]["Ki"],"d":validated[winner]["gains"]["Kd"]}
        arms[name]={"initial_pid":initial,"episode":episode,
                    "final_selection":final_choice,"final_candidate_results":validated,
                    "recommended_pid":final_choice["selected_pid"] if final_choice["status"]=="selected" else None,
                    "validation_pid":validation_pid,"validation":validated[winner]}
        validation_traces[name]=trace[winner]
        write_csv(args.out/f"{name}_training.csv",training_rows)
        write_csv(args.out/f"{name}_validation.csv",trace[winner])
        for key,rows in trace.items(): write_csv(args.out/f"{name}_{key}.csv",rows)
    for name,rows in trial_traces.items(): write_csv(args.out/f"initial_{name}.csv",rows)
    report={"tuner":args.tuner,"evidence":"real LLM run; check successful calls/fallbacks" if args.tuner=="llm" else "mock pipeline test only; not evidence of LLM superiority",
            "initialization":initialization,"original_reference":reference,"arms":arms,
            "settings":{"plant":args.plant,"controller_kind":controller_kind,"setpoint_c":setpoint,
            "dt_s":dt,"validation_duration_s":horizon,"rounds_limit":args.rounds,"samples_per_round":args.samples,
            "seed":args.seed,"noise_c_per_sample":args.noise,"gain_scale":args.gain_scale,
            "disturbance_time_s":args.disturbance_time,"disturbance_rate_c_per_s":args.disturbance_rate},
            "provider":CONFIG["LLM_PROVIDER"] if args.tuner=="llm" else "mock",
            "model":CONFIG["LLM_MODEL_NAME"] if args.tuner=="llm" else "deterministic test double"}
    serialized=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    key=CONFIG.get("LLM_API_KEY","")
    if key and key!="your-api-key-here": serialized=serialized.replace(key,"[REDACTED]")
    (args.out/"summary.json").write_text(serialized,encoding="utf-8")
    write_dashboard(args.out/"report.html",report,validation_traces)
    print(f"Selected: {initialization['selection']['selected_method']}")
    print("arm             overshoot%      IAE     tail_error    settled_s    TV    calls / rollback")
    for name,arm in arms.items():
        m=arm["validation"]["metrics"]
        print(f"{name:16} {m['overshoot_pct']:9.3f} {m['iae_c_s']:9.3f} {m['steady_state_error_c']:11.4f} {str(m['settling_time_s']):>11} {m['output_total_variation']:8.2f} {len(arm['episode']['tuner_calls'])} / {arm['episode']['engine']['rollback_count']}")
        print(f"  verified choice: {arm['final_selection']['selected_method']}; recommended PID={arm['recommended_pid']}")
    print(f"Saved {args.out.resolve()}; {report['evidence']}")
    return report


if __name__=="__main__": main()
