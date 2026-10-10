"""Reusable offline planning. No device or live actuation in this module."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlparse
from system_id import tuning_candidates
from pid_safety import should_rollback_to_best
from reference.original_system_id import system_identify as original_identify
from .models import identify
from .control import guard, guard_policy, simulate, rank
from .config import ConfigError, validate
from .process import checkpoint, quantity, public_config


def _legacy_metrics(trial):
    m = trial["metrics"]
    return {
        "avg_error": m["iae_c_s"] / max(m["duration_s"], 1e-9),
        "steady_state_error": m["tail_mae_c"],
        "overshoot": m["overshoot_pct"],
        "status": "STABLE" if m["eligible"] else "UNSTABLE",
    }


def make_tuner(cfg, base, cancelled=None):
    l = cfg["llm"]
    key = os.environ.get(l["api_key_env"], "")
    if not key and l["credentials_file"]:
        path = Path(base) / l["credentials_file"]
        if path.exists():
            try:
                credentials = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(credentials, dict):
                    raise ConfigError("local LLM credentials must be a JSON object")
                key = credentials.get("LLM_API_KEY", credentials.get("API_KEY", ""))
            except (OSError, ValueError, TypeError) as exc:
                raise ConfigError("cannot read local LLM credentials") from exc
    if not key:
        raise ConfigError(
            "LLM enabled but no API key available in environment/local credentials file"
        )
    if not isinstance(key, str):
        raise ConfigError("LLM API key must be a string")
    from llm.client import LLMTuner
    from .llm_tuner import ConfiguredTuner

    options = {"max_tokens": l["max_output_tokens"]}
    anthropic = (
        l["provider"] in ("anthropic", "anthropic_native", "claude_native")
        or l["provider"] == "auto"
        and urlparse(l["base_url"]).hostname == "api.anthropic.com"
    )
    if not anthropic:
        if l["json_output"]:
            options["response_format"] = {"type": "json_object"}
        if (
            urlparse(l["base_url"]).hostname == "api.deepseek.com"
            and l["deepseek_thinking"] != "provider_default"
        ):
            options["extra_body"] = {"thinking": {"type": l["deepseek_thinking"]}}
    return ConfiguredTuner(
        LLMTuner(
            key,
            l["base_url"],
            l["model"],
            provider=l["provider"],
            timeout=l["timeout_s"],
            emit_console=False,
            debug_output=False,
            max_attempts=l["max_attempts"],
            request_options=options,
            **({'abort_check': cancelled} if cancelled is not None else {}),
        )
    )


def refine(cfg, initial, tuner, identified_model=None, cancelled=None, progress=None):
    key = lambda t: rank(t, cfg['tuning'].get('selection_priority', 'accuracy'))
    current = initial
    best = initial
    best_deliverable = initial
    history = []
    stable = 0
    # Each proposal is evaluated from the same initial state for the full task.
    for index in range(cfg["tuning"]["rounds"]):
        checkpoint(cancelled)
        if progress:
            progress({'type':'progress','message':f'LLM 调优 {initial.get("name", "")}：第 {index+1} 轮'})
        context = {
            key: cfg[key]
            for key in ("task", "model", "actuator", "controller", "evaluation", "simulation")
        }
        context.update(
            identified_fopdt=identified_model,
            control_domain=quantity(cfg),
            pid_limits=cfg["controller"]["limits"],
            pid_limits_are_runtime_enforced=True,
            guardrail_policy=guard_policy(cfg, "llm"),
            selection_priority=cfg['tuning'].get('selection_priority', 'accuracy'),
            instruction="Return positive seconds-based parallel gain magnitudes p/i/d. The process direction is applied separately. No actuator commands. Evaluate a complete setpoint step; do not infer safety from a short window.",
        )
        response = tuner.analyze(
            json.dumps(
                {
                    "current_pid": current["pid"],
                    "metrics": current["metrics"],
                    "samples": current["samples"][
                        :: max(1, len(current["samples"]) // cfg["tuning"]["samples_per_round"])
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(history, ensure_ascii=False),
            tuning_mode="generic",
            prompt_context=context,
        )
        if not isinstance(response, dict):
            diagnostic = dict(getattr(tuner, 'last_request_diagnostic', {}) or {})
            history.append(
                {"round": index + 1, "event": "llm_unavailable", "action": "retain_best",
                 "diagnostic": diagnostic, "reason": diagnostic.get('message', 'LLM 未获得可用参数建议。')}
            )
            if progress:
                progress({'type':'progress','message':history[-1]['reason'] + ' 已保留当前最佳参数。'})
            break
        candidate = response.get("pid", response.get("parameters", response))
        if not isinstance(candidate, dict) or not all(k in candidate for k in ("p", "i", "d")):
            history.append(
                {"round": index + 1, "event": "invalid_llm_schema", "action": "retain_best"}
            )
            break
        applied, notes = guard(cfg, current["pid"], candidate)
        checkpoint(cancelled)
        trial = simulate(cfg, applied, cancelled=cancelled)
        rollback = should_rollback_to_best(_legacy_metrics(trial), _legacy_metrics(best)) or (
            best["metrics"]["eligible"] and not trial["metrics"]["eligible"]
        )
        history.append(
            {
                "round": index + 1,
                "requested_pid": {
                    k: (
                        candidate[k]
                        if isinstance(candidate[k], (str, int, float, bool, type(None)))
                        and str(candidate[k]).lower() not in ("nan", "inf", "-inf")
                        else repr(candidate[k])
                    )
                    for k in ("p", "i", "d")
                },
                "applied_pid": applied,
                "guard_notes": notes,
                "guard_policy": guard_policy(cfg, "llm"),
                "metrics": trial["metrics"],
                "rollback": rollback,
                "reason": str(response.get("analysis_summary", response.get("reason", ""))),
                "llm_diagnostic": dict(getattr(tuner, 'last_request_diagnostic', {}) or {}),
            }
        )
        if key(trial) < key(best):
            best = trial
        delivery_pid, delivery_notes = guard(cfg, cfg["controller"]["initial_pid"], trial["pid"], stage="delivery")
        delivery_trial = simulate(cfg, delivery_pid) if delivery_pid != trial["pid"] else trial
        history[-1]["delivery_guard_notes"] = delivery_notes
        history[-1]["delivery_guard_policy"] = guard_policy(cfg, "delivery")
        history[-1]["delivery_metrics"] = delivery_trial["metrics"]
        if key(delivery_trial) < key(best_deliverable):
            best_deliverable = delivery_trial
        current = best if rollback else trial
        stable = (
            stable + 1
            if trial["metrics"]["eligible"]
            and trial["metrics"]["iae_c_s"] / trial["metrics"]["duration_s"]
            <= cfg["tuning"]["average_error_threshold_c"]
            else 0
        )
        if stable >= cfg["tuning"]["stable_rounds"]:
            break
        if (response.get("done") is True or response.get("status") == "DONE") and trial["metrics"][
            "eligible"
        ]:
            break
    return best_deliverable, history


def plan(cfg, base, tuner=None, identified=None, cancelled=None, progress=None):
    checkpoint(cancelled)
    if progress:
        progress({'type':'progress','message':'检查对象模型／辨识历史数据'})
    if identified is None:
        cfg, identification, data = identify(cfg, base)
    else:
        identification, data = identified
    validate(cfg)
    m = identification["model"]
    candidates = []
    unavailable = []
    if cfg['model']['source'] == 'manual':
        pid = cfg['controller']['initial_pid']
        raw = {'USER_PID':dict(Kp=pid['p'], Ki=pid['i'], Kd=pid['d'], method='user initial PID')}
        include = ['USER_PID']
    elif cfg['model']['type'] == 'integrating':
        lam = cfg['algorithms']['simc_lambda_s']
        lam = max(m['theta'], cfg['controller']['sample_time_s']) if lam is None else lam
        kp = 1/(abs(m['K'])*(lam+m['theta']))
        ti = 4*(lam+m['theta'])
        raw = {'SIMC_PI':dict(Kp=kp, Ki=kp/ti, Kd=0, Ti=ti, Td=0, lambda_s=lam, model='IPDT')}
        include = [name for name in cfg['algorithms']['include'] if name == 'SIMC_PI']
        unavailable.extend({'name':n, 'reason':'Z-N FOPDT公式不适用于积分模型'} for n in cfg['algorithms']['include'] if n != 'SIMC_PI')
    else:
        raw = tuning_candidates(m["K"], m["tau"], m["theta"], cfg["algorithms"]["simc_lambda_s"])
        include = cfg['algorithms']['include']

    def evaluate(name, parameters):
        checkpoint(cancelled)
        if progress:
            progress({'type':'progress','message':f'护栏检查并仿真：{name}'})
        if "error" in parameters:
            unavailable.append({"name": name, "reason": parameters["error"]})
            return None
        requested = {k: abs(parameters[v]) for k, v in zip(("p", "i", "d"), ("Kp", "Ki", "Kd"))}
        applied, notes = guard(cfg, cfg["controller"]["initial_pid"], requested, stage="initial")
        result = simulate(cfg, applied, cancelled=cancelled)
        result.update(name=name, requested_pid=requested, guard_notes=notes, formula=parameters,
                      guard_policy=guard_policy(cfg, "initial"))
        if progress:
            progress({'type':'candidate','name':name,'pid':applied,'requested_pid':requested,
                      'metrics':result['metrics'],'guard_notes':notes,
                      'samples':result['samples'][::max(1,len(result['samples'])//500)]})
        return result

    for name in include:
        result = evaluate(name, raw[name])
        if result:
            candidates.append(result)
    if not candidates:
        raise ConfigError("no calculable tuning candidates")
    key = lambda t: rank(t, cfg['tuning'].get('selection_priority', 'accuracy'))
    selected = min(candidates, key=key)
    if cfg["llm"]["enabled"] and tuner is None:
        tuner = make_tuner(cfg, base, cancelled=cancelled) if cancelled is not None else make_tuner(cfg, base)
    results = []
    legacy_status = 'disabled' if not cfg['tuning'].get('include_legacy_route', True) else 'unavailable'
    legacy_reference = None
    if data is not None and (cfg['tuning'].get('include_legacy_route', True) or cfg['tuning']['compare_original']):
        try:
            old = original_identify(*data[:3])
            parameters = old.get("ziegler_nichols", {}).get(
                "PID", {"error": old.get("error", "original identifier unavailable")}
            )
            result = evaluate("original_zn", parameters)
            if result:
                if cfg['tuning']['compare_original']:
                    results.append(dict(name='original_zn', initial_method='ZN_PID', initial=result,
                        final=result, history=[], llm_enabled=False, role='identification_reference',
                        label='原辨识 Z-N 公式参考（不是原版完整调优）'))
                if cfg['tuning'].get('include_legacy_route', True):
                    from pid_safety import apply_pid_guardrails
                    from .legacy_route import run_legacy_route
                    applied, notes = apply_pid_guardrails(cfg['controller']['initial_pid'], result['requested_pid'],
                        limits=cfg['controller']['limits'], global_max_increase_ratio=cfg['controller']['global_max_increase_ratio'])
                    initial = simulate(cfg, applied, cancelled=cancelled)
                    initial.update(name='legacy_route', guard_notes=notes, requested_pid=result['requested_pid'])
                    best, history, execution = (run_legacy_route(cfg, initial, tuner, cancelled, progress)
                        if cfg['llm']['enabled'] else (initial, [], dict(completed=False, scope='LLM disabled; guarded original initialization only')))
                    legacy_status = 'completed' if execution['completed'] else ('llm_disabled' if not cfg['llm']['enabled'] else 'incomplete')
                    legacy_reference = dict(name='legacy_route', label='旧版完整调优路线' if cfg['llm']['enabled'] else '旧版初始化（LLM关闭）',
                        initial_method='ZN_PID', initial=initial, final=best, history=history,
                        llm_enabled=cfg['llm']['enabled'], role='route', family='legacy', execution=execution)
                    results.append(legacy_reference)
        except Exception as exc:
            checkpoint(cancelled)
            unavailable.append({"name": "original_zn", "reason": type(exc).__name__})
    names = {'ZN_PID':'corrected_zn', 'ZN_PI':'zn_pi_route', 'SIMC_PI':'simc_route', 'USER_PID':'user_route'}
    labels = {'ZN_PID':'修正 Z-N PID 路线', 'ZN_PI':'修正 Z-N PI 路线', 'SIMC_PI':'SIMC PI 路线', 'USER_PID':'用户 PID 路线'}
    # No preselected method is discarded: every calculable method has its own
    # refinement history, including a SIMC initially slower than Z-N.
    for candidate in candidates:
        arm = dict(candidate, name=names.get(candidate['name'], candidate['name']))
        checkpoint(cancelled)
        best, history = refine(cfg, arm, tuner, m, cancelled, progress) if cfg["llm"]["enabled"] else (arm, [])
        # Device writes retain the final jump check; offline formula comparisons
        # retain numeric/absolute bounds without an arbitrary default-PID cap.
        deliverable, delivery_notes = guard(cfg, cfg["controller"]["initial_pid"], best["pid"], stage="delivery")
        if deliverable != best["pid"]:
            checked = simulate(cfg, deliverable)
            best = min((arm, checked), key=key)
        results.append(
            {
                "name": arm["name"],
                "initial_method": candidate['name'],
                "label": labels.get(candidate['name'], candidate['name']),
                "role": 'route', "family": 'new',
                "initial": arm,
                "final": best,
                "history": history,
                "llm_enabled": cfg["llm"]["enabled"],
                "final_delivery_guard_notes": delivery_notes,
                "final_delivery_guard_policy": guard_policy(cfg, "delivery"),
            }
        )
        if progress:
            progress({'type':'arm','name':arm['name'],'pid':best['pid'],
                      'metrics':best['metrics'],'history':history,
                      'samples':best['samples'][::max(1,len(best['samples'])//500)]})
    hybrid_status = 'disabled'
    if cfg['tuning'].get('include_corrected_legacy_route', True):
        seed = next((c for c in candidates if c['name'] == 'ZN_PID'), None)
        hybrid_status = 'llm_disabled' if not cfg['llm']['enabled'] else 'unavailable'
        if cfg['llm']['enabled'] and seed is not None and m['K'] > 0:
            try:
                from .legacy_route import run_legacy_route
                initial = dict(seed, name='corrected_legacy_route')
                best, history, execution = run_legacy_route(cfg, initial, tuner, cancelled, progress)
                hybrid_status = 'completed' if execution['completed'] else 'incomplete'
                results.append(dict(name='corrected_legacy_route', label='混合路线：修正初值＋旧版连续调优',
                    initial_method='ZN_PID', initial=initial, final=best, history=history,
                    llm_enabled=True, role='route', family='hybrid', execution=execution))
                if progress:
                    progress({'type':'arm','name':'corrected_legacy_route','pid':best['pid'],
                        'metrics':best['metrics'],'history':history,
                        'samples':best['samples'][::max(1,len(best['samples'])//500)]})
            except Exception as exc:
                checkpoint(cancelled)
                unavailable.append({'name':'corrected_legacy_route','reason':type(exc).__name__})
    routes = [r for r in results if r.get('role') == 'route']
    # On an exact tie retain the old baseline; adding a duplicate does not count
    # as an improvement. Eligibility and the selected objective are identical.
    winner = min(routes, key=lambda r: (key(r['final']), {'legacy':0, 'new':1, 'hybrid':2}[r['family']]))
    final = winner['final']
    qualified = final['metrics']['eligible']
    inherited = winner['family'] == 'legacy'
    priority = cfg['tuning'].get('selection_priority', 'accuracy')
    tied = bool(legacy_reference and key(final) == key(legacy_reference['final']))
    reason = ('新路线没有在当前评价标准下超过旧版，保留旧版路线。' if inherited else
        '新路线按当前评价标准优于本次旧版候选。' if legacy_reference and key(final) < key(legacy_reference['final']) else
        '旧版路线未参与／不可用，本次只在可用的新路线中选优。')
    if winner['family'] == 'hybrid':
        reason = '本次采用混合路线：修正 Z-N 初值，再使用旧版连续调优核心；优势不能归因于纯新版 LLM 策略。'
    if not qualified:
        reason = '所有已验证路线均未达标，不输出可用 PID。'
    decision = dict(selected_route=winner['name'], selected_label=winner['label'], family=winner['family'],
        used_legacy_route=inherited if qualified else None, priority=priority, reason=reason,
        used_legacy_tuning_core=winner['family'] in ('legacy','hybrid') if qualified else None,
        hybrid_status=hybrid_status, qualified=qualified, legacy_status=legacy_status,
        legacy_reference_qualified=bool(legacy_reference and legacy_reference['final']['metrics']['eligible']),
        baseline_comparison_available=legacy_status == 'completed',
        strictly_improved_vs_legacy=bool(qualified and legacy_status == 'completed' and key(final) < key(legacy_reference['final'])),
        exact_tie_with_legacy=tied, guarantee_scope='this run, common configured simulation and selection priority',
        routes=[dict(name=r['name'], label=r['label'], family=r['family'], metrics=r['final']['metrics'],
            score=[v if not isinstance(v, float) or math.isfinite(v) else None for v in key(r['final'])]) for r in routes])
    # Selected is a compatibility/view alias, not another LLM call or another
    # independent candidate. Its provenance is always the actual winning route.
    results.append(dict(name='selected', label='最终跨路线选优', role='selection', family=winner['family'],
        initial_method=winner['initial_method'], initial=winner['initial'], final=final,
        history=[], llm_enabled=cfg['llm']['enabled'], selected_route=winner['name']))
    for result in results:
        result.setdefault('final_delivery_guard_notes', [])
        result.setdefault('final_delivery_guard_policy', guard_policy(cfg, 'delivery'))
    if progress:
        progress({'type':'selection','message':winner['label']+'：'+reason, 'decision':decision})
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": hashlib.sha256(
            json.dumps(cfg, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        "config": cfg,
        "public_config": public_config(cfg),
        "process": quantity(cfg),
        "guardrail_context": {
            "configured_policy": cfg["controller"]["guardrail_policy"],
            "initial_candidates": guard_policy(cfg, "initial"),
            "llm_rounds": guard_policy(cfg, "llm"),
            "final_delivery": guard_policy(cfg, "delivery"),
            "reference_pid": cfg["controller"]["initial_pid"],
        },
        "identification": identification,
        "candidates": candidates,
        "unavailable": unavailable,
        "selected_method": winner['initial_method'],
        "initial_selected_method": selected['name'],
        "selection": decision,
        "arms": results,
        "recommended": final if final["metrics"]["eligible"] else None,
        "recommendation_status": (
            "qualified_in_configured_simulation"
            if final["metrics"]["eligible"]
            else "no_qualified_candidate"
        ),
    }
