"""Portable report: no CDN, network requests or embedded credentials."""

import csv
import html
import json
from pathlib import Path
from .process import quantity, FAILURE_LABELS


def write_report(result, directory, save_csv=True):
    process = quantity(result['config'])
    unit = html.escape(process['unit'])
    name = html.escape(process['name'])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    recommendation = result["recommended"]
    (directory / "pid.json").write_text(
        json.dumps(
            {
                "status": result["recommendation_status"],
                "created_at_utc": result["created_at_utc"],
                "config_sha256": result["config_sha256"],
                "device_status": result.get("device_status", "not_requested"),
                "pid": recommendation["export_pid"] if recommendation else None,
                "internal_parallel_seconds_magnitudes": (
                    recommendation["pid"] if recommendation else None
                ),
                "output_unit": result["config"]["actuator"]["unit"],
                "process": process,
                "sample_time_s": result["config"]["controller"]["sample_time_s"],
                "validation_scope": "configured offline simulation",
                "selection": result.get('selection'),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if save_csv:
        for arm in result["arms"]:
            with (directory / (arm["name"] + ".csv")).open(
                "w", encoding="utf-8-sig", newline=""
            ) as handle:
                rows = arm["final"]["samples"]
                rows = [{k:v for k,v in r.items() if k not in ('temperature_c','measured_temperature_c')} for r in rows]
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        with (directory / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            rows = [
                {
                    "name": arm["name"],
                    "initial_method": arm["initial_method"],
                    "route_role": arm.get('role'),
                    "route_family": arm.get('family'),
                    "final_selected_route": (result.get('selection') or {}).get('selected_route'),
                    "used_legacy_route": (result.get('selection') or {}).get('used_legacy_route'),
                    **arm["final"]["metrics"],
                }
                for arm in result["arms"]
            ]
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    target = result["config"]["task"]["target_temperature_c"]
    colors = ["#df6b45", "#7c64dc", "#168774"]

    def chart(field, label):
        series = [arm["final"]["samples"] for arm in result["arms"]]
        max_t = max(rows[-1]["time_s"] for rows in series)
        values = [row[field] for rows in series for row in rows]
        if field == "temperature_c":
            values.append(target)
        low, high = min(values), max(values)
        pad = max((high - low) * 0.05, 1)
        low -= pad
        high += pad

        def point(row):
            return f"{60+row['time_s']/max_t*860:.2f},{280-(row[field]-low)/(high-low)*230:.2f}"

        lines = []
        for index, rows in enumerate(series):
            thin = rows[:: max(1, len(rows) // 1500)] + [rows[-1]]
            lines.append(
                f'<polyline fill="none" stroke="{colors[index%3]}" stroke-width="2" points="'
                + " ".join(point(r) for r in thin)
                + '"/>'
            )
        if field == "temperature_c":
            y = 280 - (target - low) / (high - low) * 230
            lines.append(
                f'<path d="M60 {y}H920" stroke="#555" stroke-dasharray="6 5"/><text x="760" y="{y-5}">目标 {target:g} {unit}</text>'
            )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 320" role="img" aria-label="{html.escape(label)}"><path d="M60 40V280H920" fill="none" stroke="#666"/><text x="5" y="55">{high:.1f}</text><text x="5" y="280">{low:.1f}</text><text x="60" y="305">0 s</text><text x="830" y="305">{max_t:g} s</text>'
            + "".join(lines)
            + "</svg>"
        )
        (directory / (field + ".svg")).write_text(svg, encoding="utf-8")
        return f"<h2>{html.escape(label)}</h2>" + svg

    rows = []
    for index, arm in enumerate(result["arms"]):
        m = arm["final"]["metrics"]
        p = arm["final"]["export_pid"]
        cells = [
            arm.get('label', arm["name"]),
            arm["initial_method"],
            f"{m['overshoot_pct']:.3f}",
            f"{m['iae_c_s']:.3f}",
            f"{m['tail_mae_c']:.5f}",
            "未稳定" if m["settling_time_s"] is None else f"{m['settling_time_s']:.1f}",
            f"{m['output_tv']:.3f}",
            "达标" if m["eligible"] else ", ".join(FAILURE_LABELS.get(f,f) for f in m["failures"]),
        ]
        rows.append(
            "<tr>" + "".join("<td>" + html.escape(str(cell)) + "</td>" for cell in cells) + "</tr>"
        )
        rows.append(
            '<tr><td colspan="8"><code>'
            + html.escape(json.dumps(p, ensure_ascii=False))
            + "</code></td></tr>"
        )
    status = (
        "已生成仿真合格参数"
        if recommendation
        else "无合格建议：请检查未达标项，修改模型或整定设置后重试"
    )
    data = html.escape(
        json.dumps(
            {k: result[k] for k in ("identification", "unavailable", "selected_method")},
            ensure_ascii=False,
            indent=2,
        )
    )
    doc = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PID Workbench</title><style>body{max-width:1100px;margin:35px auto;padding:0 20px;font:16px/1.6 system-ui;color:#233}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}svg{width:100%;background:#f8faf9}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}.legend{padding:10px}</style>' + f'<h1>{name} PID 对比报告</h1>'
    doc += f'<p>任务：{result["config"]["task"]["initial_temperature_c"]:g} → {target:g} {unit}。模式：{html.escape(result["config"]["mode"])}。{status}。</p><p>原辨识公式参考不等于旧版完整调优路线。旧版、Z-N PID、Z-N PI、SIMC PI 分别调优，先检查护栏并统一复测，再跨路线选优。selected 仅展示最终赢家，不重复调用 LLM。积分或手动模型会标明不适用的路线。</p>'
    context = result.get("guardrail_context")
    if context:
        labels = {"absolute": "数值与绝对范围检查", "relative": "数值、绝对范围与相对增幅检查"}
        doc += '<p><strong>本次护栏：</strong>' + '；'.join(
            html.escape(label + '：' + labels[context[key]])
            for label, key in (("初始公式候选", "initial_candidates"), ("LLM每轮", "llm_rounds"), ("最终参数", "final_delivery"))
        ) + '。参数检查通过后仍须完整任务仿真达标；test 模式不连接设备。</p>'
    selection = result.get('selection')
    if selection:
        doc += '<p><strong>最终路线：' + html.escape(selection['selected_label']) + '</strong>。' + html.escape(selection['reason']) + ' 评价标准：' + html.escape(selection['priority']) + '。旧版路线状态：' + html.escape(selection['legacy_status']) + '。</p>'
    doc += (
        "<p>生成时间："
        + html.escape(result["created_at_utc"])
        + "。设备状态："
        + html.escape(result.get("device_status", "not_requested"))
        + '。</p><p><a href="pid.json">查看最终 PID 与单位</a> · <a href="summary.json">查看完整记录</a></p>'
    )
    if result["config"]["mode"] == "use":
        doc += '<p><strong>离线合格状态与设备写入状态分别记录；写入是否成功请同时查看 <a href="device_audit.json">设备审计</a>。</strong></p>'
    doc += (
        "<p>曲线和指标展示各组最终保留的最佳参数；本报告证明配置模型上的表现，不能证明所有真实化工对象上更优。</p><table><tr>"
        + "".join(
            "<th>" + s + "</th>"
            for s in [
                "组别",
                "初始算法",
                "超调 %",
                f"IAE {unit}·s",
                f"末段 MAE {unit}",
                "调节时间 s",
                "输出变化总量",
                "评价",
            ]
        )
        + "</tr>"
        + "".join(rows)
        + "</table>"
    )
    doc += (
        '<div class="legend">'
        + "　".join(
            f'<span style="color:{colors[i%3]}">● {html.escape(a["name"])}</span>'
            for i, a in enumerate(result["arms"])
        )
        + "</div>"
    )
    doc += (
        chart("temperature_c", process['name'] + '响应（' + process['unit'] + '）')
        + chart("output", "控制输出（" + result["config"]["actuator"]["unit"] + "）")
        + "<details><summary>辨识和算法选择详情</summary><pre>"
        + data
        + "</pre></details><p>IAE 越小表示累计温差越小；末段 MAE 越小表示最终越接近目标；调节时间须持续保持在配置温差带内；输出变化总量越小表示动作更平稳。先看是否达标，再比较 IAE 和输出变化。</p></html>"
    )
    candidates = "<h2>Z-N / SIMC 初始候选</h2><p>下表 p/i/d 均为秒制并联式非负幅值，过程方向由 K 决定。实际对比使用护栏后的参数。</p><table><tr><th>候选</th><th>公式计算 p/i/d</th><th>护栏后 p/i/d</th><th>超调 %</th><th>IAE</th><th>结果</th></tr>"
    for trial in result["candidates"]:
        m = trial["metrics"]
        values = [
            trial["name"],
            ", ".join(f'{trial["requested_pid"][k]:.6g}' for k in ("p", "i", "d")),
            ", ".join(f'{trial["pid"][k]:.6g}' for k in ("p", "i", "d")),
            f'{m["overshoot_pct"]:.3f}',
            f'{m["iae_c_s"]:.3f}',
            "达标" if m["eligible"] else ", ".join(FAILURE_LABELS.get(f,f) for f in m["failures"]),
        ]
        candidates += (
            "<tr>" + "".join("<td>" + html.escape(str(v)) + "</td>" for v in values) + "</tr>"
        )
        if trial["guard_notes"]:
            candidates += (
                '<tr><td colspan="6">' + html.escape("；".join(trial["guard_notes"])) + "</td></tr>"
            )
    candidates += (
        "</table><details><summary>LLM 各轮及最终写入增幅检查</summary><pre>"
        + html.escape(
            json.dumps(
                [
                    {k: arm[k] for k in ("name", "history", "final_delivery_guard_notes")}
                    for arm in result["arms"]
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        + "</pre></details>"
    )
    doc = doc.replace("</html>", candidates + "</html>")
    (directory / "report.html").write_text(doc, encoding="utf-8")
    return directory / "report.html"
