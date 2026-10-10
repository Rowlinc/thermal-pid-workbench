"""Run configurable scenarios and retain all arms, including failed trials."""

import argparse
import csv
import html
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pid_project import create_run_directory, run
from thermal_pid.config import load_project, ConfigError


def report_link(report, directory):
    try:
        return Path(os.path.relpath(report, directory)).as_posix()
    except ValueError:
        # Windows cannot form a relative path between different drives.
        return Path(report).resolve().as_uri()


def save_summary(directory, rows, failures, planned, candidate_failures=None, completed_names=None):
    payload = {"planned_scenarios": planned, "completed_scenarios": len(set(completed_names) if completed_names is not None else {r['scenario'] for r in rows}),
               "rows": rows, "execution_failures": failures, "candidate_failures": candidate_failures or []}
    (directory / "suite.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if rows:
        with (directory / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    completed = payload['completed_scenarios']
    lines = ["# 多场景完整调优路线对比", "", f"计划 {planned} 个场景，已完成 {completed} 个场景，执行失败 {len(failures)} 个。", "",
        "当前各路线统一使用整组拒绝护栏。护栏拒绝不代表算法计算错误或控制效果差；未进行仿真时没有性能指标。候选拒绝与仿真错误见 suite.json 的 candidate_failures。旧核心也使用当前护栏，不能把本版结果称为未修改原项目的独立运行。", "",
        "legacy_route 保留原项目连续调优核心，在共用对象和控制器上运行；不是原项目原生温控模拟器的独立实测。original_zn 只是不经 LLM 调优的原辨识公式参考。selected 是最终胜出路线的别名，不再独立调用 LLM。", "",
        "每个场景内对象、任务、执行器、评价门槛、LLM 模型与每路线最大轮数相同。新旧初始化和调优策略不同，这是比较对象本身。先判断达标，再按该场景的 accuracy/smooth/speed 优先级比较。", "",
        "## 各场景最终选择", "", "| 场景 | 优先级 | LLM | 最终路线 | 达标 | 旧版覆盖状态 | 严格优于完整旧版 |", "|---|---|---|---|---|---|---|"]
    groups = {name: {r['arm']:r for r in rows if r['scenario']==name} for name in dict.fromkeys(r['scenario'] for r in rows)}
    for name, arms in groups.items():
        r=arms.get('selected',next(iter(arms.values())))
        improvement=r.get('strictly_improved_vs_legacy')
        lines.append(f"| {name} | {r.get('selection_priority','accuracy')} | {r['llm_enabled']} | {r.get('final_selected_route','未知')} | {r['eligible']} | {r.get('legacy_status','未知')} | {'未形成完整对照' if r.get('legacy_status')!='completed' or r.get('budget_limited') else improvement} |")
    lines += ["", "## 全部路线指标（包含未达标结果）", "",
        "IAE 单位为被控量单位×秒；末段误差使用各场景的被控量单位；输出 TV 是执行器输出单位的累计变化，不是能耗。", "",
        "| 场景 | 路线 | 起点 | 达标 | 超调% | IAE | 末段MAE | 调节s | 输出TV | 参数改变 | 参数建议/历史记录 | 报告 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|"]
    for r in rows:
        settle = '未稳定' if r['settling_time_s'] is None else f"{r['settling_time_s']:.1f}"
        lines.append(f"| {r['scenario']} | {r['arm']} | {r['initial_method']} | {r['eligible']} | {r['overshoot_pct']:.4f} | {r['iae_c_s']:.2f} | {r['tail_mae_c']:.5f} | {settle} | {r['output_tv']:.2f} | {r['parameters_changed']} | {r['valid_llm_suggestions']}/{r['llm_history_records']} | [查看]({r['report']}) |")
    lines += ["", "## 分项比较：新版最佳路线 vs 旧版路线", "", "正数表示新版该指标更小；未达标时不能将某个指标更小视为整体成功。旧版完整调优是否执行完毕须结合覆盖状态。", "",
        "| 场景 | 新版最佳路线 | 旧版达标/新版达标 | IAE降低% | 超调降低% | TV降低% | 调节时间降低% |", "|---|---|---|---:|---:|---:|---:|"]
    counts={'legacy':0,'new':0,'hybrid':0,'unqualified':0,'not_comparable':0}
    for name, arms in groups.items():
        selected=arms.get('selected',{})
        if not selected.get('eligible'): counts['unqualified']+=1
        elif selected.get('legacy_status')!='completed' or selected.get('budget_limited'): counts['not_comparable']+=1
        else: counts[selected.get('family','legacy' if selected.get('used_legacy_route') else 'new')]+=1
        baseline=arms.get('legacy_route')
        if not baseline: continue
        options=[r for r in arms.values() if r.get('family')=='new' and r.get('role')=='route']
        if not options: continue
        def key(r):
            settling=r['settling_time_s'] if r['settling_time_s'] is not None else float('inf')
            objective={'accuracy':(r['iae_c_s'],r['output_tv'],settling),'smooth':(r['overshoot_pct'],r['output_tv'],r['iae_c_s'],settling),'speed':(settling,r['iae_c_s'],r['output_tv'])}[r.get('selection_priority','accuracy')]
            return (not r['eligible'],len([x for x in r['failures'].split(',') if x]),*objective)
        new=min(options,key=key)
        def reduction(k):
            b,n=baseline[k],new[k]
            return '不适用' if b is None or n is None or b==0 else f"{(b-n)/b*100:.2f}"
        lines.append(f"| {name} | {new['arm']} | {baseline['eligible']}/{new['eligible']} | {reduction('iae_c_s')} | {reduction('overshoot_pct')} | {reduction('output_tv')} | {reduction('settling_time_s')} |")
    payload['selection_counts']=counts
    (directory / "suite.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding='utf-8')
    lines += ["", "## 本批次选择统计", "", f"完整且达标对照中：旧版胜出 {counts['legacy']}，纯新路线胜出 {counts['new']}，混合路线胜出 {counts['hybrid']}；未达标 {counts['unqualified']}，未形成完整旧版对照 {counts['not_comparable']}。", "",
        "## 解释限制", "", "- 本批次每场景一次运行，不能证明长期胜率或所有现场适用性；LLM 有随机性。",
        "- 公式辨识修正、初始化护栏、新增 PI 候选与 LLM 策略共同影响结果，应看每路线初值与最终值，不能把所有优势归因于 SIMC。",
        "- parameters_changed=false 表示该路线最终仍使用起点参数；有效参数建议或回滚记录不代表最终改善。",
        "- 不同对象的 IAE 有不同单位、任务幅度和时长，不能直接跨场景比大小。",
        "- 每路线最大轮数相同，实际网络调用数量可能因提前完成、回滚或失败不同；完整请求成本由外部预算记录记录。", ""]
    if failures: lines += ["## 执行失败", ""]+[f"- {f['config']}：{f['error_type']}。" for f in failures]
    (directory/'comparison.md').write_text('\n'.join(lines),encoding='utf-8')
    keys=('scenario','arm','eligible','overshoot_pct','iae_c_s','tail_mae_c','settling_time_s','output_tv','final_selected_route')
    cells=''.join('<tr>'+''.join(f'<td>{html.escape(str(r[k]))}</td>' for k in keys)+f'<td><a href="{html.escape(r["report"],quote=True)}">曲线和历史</a></td></tr>' for r in rows)
    header=''.join(f'<th>{v}</th>' for v in ('场景','路线','达标','超调%','IAE','末段误差','调节s','输出TV','最终选择','报告'))
    page=f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>完整路线对比</title><style>body{{font-family:system-ui;padding:24px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:8px}}th{{background:#edf2f7}}</style><h1>完整调优路线对比</h1><p>完成 {completed}/{planned}。<a href="comparison.md">分项比较和解释</a> · <a href="comparison.csv">CSV</a></p><p>旧版连续调优核心使用共用对象与控制器。原公式参考不是完整旧版结果。</p><table><tr>{header}</tr>{cells}</table></html>'
    (directory/'index.html').write_text(page,encoding='utf-8')


def main(argv=None):
    from core.config import ensure_utf8_console

    ensure_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configs', nargs='*', help='configuration paths; default: examples/scenarios/*.json')
    parser.add_argument('--scenario-set', choices=['scenarios','route_benchmarks'], default='scenarios', help='built-in configuration folder, used when no explicit configs are supplied')
    parser.add_argument('--llm', choices=['configured', 'on', 'off'], default='configured')
    args = parser.parse_args(argv)
    configs = [Path(p).resolve() for p in args.configs] if args.configs else sorted((ROOT / 'examples' / args.scenario_set).glob('*.json'))
    if not configs:
        parser.error('no scenario configurations found')
    directory = create_run_directory(ROOT / 'results/comparison_suite')
    print(f'Suite directory: {directory}', flush=True)
    rows, failures, candidate_failures, completed_names = [], [], [], []
    for index, path in enumerate(configs, 1):
        print(f'[{index}/{len(configs)}] Starting {path.stem}', flush=True)
        started = time.monotonic()
        try:
            cfg, base = load_project(path)
            if cfg['mode'] != 'test':
                raise ConfigError('comparison suite only accepts test mode')
            if args.llm != 'configured':
                cfg['llm']['enabled'] = args.llm == 'on'
            result, output = run(cfg, base)
            completed_names.append(cfg['name'])
            candidate_failures.extend(dict(scenario=cfg['name'], **record) for record in
                result.get('rejected_candidates', []) + result.get('failed_candidates', []))
            elapsed = time.monotonic() - started
            for arm in result['arms']:
                m = arm['final']['metrics']
                history = arm['history']
                rows.append(dict(scenario=cfg['name'], value_unit=cfg['process']['unit'], arm=arm['name'], initial_method=arm['initial_method'],
                                 family=arm.get('family','reference'), role=arm.get('role',''),
                                 status=arm['final'].get('status'),
                                 used_legacy_tuning_core=result.get('selection',{}).get('used_legacy_tuning_core'),
                                 selection_priority=cfg['tuning']['selection_priority'],
                                 legacy_status=result.get('selection',{}).get('legacy_status'),
                                 final_selected_route=result.get('selection',{}).get('selected_route'),
                                 used_legacy_route=result.get('selection',{}).get('used_legacy_route'),
                                 strictly_improved_vs_legacy=result.get('selection',{}).get('strictly_improved_vs_legacy'),
                                 eligible=m['eligible'], failures=','.join(m['failures']),
                                 **{k:m[k] for k in ('max_temperature_c','overshoot_pct','settling_time_s','iae_c_s','tail_mae_c','output_tv','saturation_fraction')},
                                 **arm['final']['pid'], initial_iae_c_s=arm['initial']['metrics']['iae_c_s'],
                                 initial_eligible=arm['initial']['metrics']['eligible'],
                                 parameters_changed=arm['initial']['pid'] != arm['final']['pid'],
                                 guardrail_policy=cfg['controller']['guardrail_policy'],
                                 initial_guard_policy=result['guardrail_context']['initial_candidates'],
                                 final_guard_policy=result['guardrail_context']['final_delivery'],
                                 llm_enabled=cfg['llm']['enabled'], llm_history_records=len(history),
                                 valid_llm_suggestions=sum(h.get('applied_pid') is not None for h in history),
                                 rejected_llm_suggestions=sum(h.get('event')=='guardrail_rejected' for h in history),
                                 scenario_elapsed_s=round(elapsed, 3), config_sha256=result['config_sha256'],
                                 report=report_link(output / 'report.html', directory)))
                print(f"  {arm['name']}: eligible={m['eligible']} IAE={m['iae_c_s']:.2f} settling={m['settling_time_s']}", flush=True)
        except (ConfigError, RuntimeError, OSError, ValueError, ImportError) as exc:
            failures.append({'config': path.name, 'error_type': type(exc).__name__})
            print(f'  Failed: {type(exc).__name__}', flush=True)
        save_summary(directory, rows, failures, len(configs), candidate_failures, completed_names)
    print(f'Summary: {directory / "index.html"}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
