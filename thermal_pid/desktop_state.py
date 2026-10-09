"""Local desktop configuration, visual field metadata and private credentials."""

from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path
import re
import uuid
import time

from .config import DEFAULTS, _merge, validate
from .config_comments import COMMENTS, parse_commented_json

ENUMS = {
    'mode': ['test', 'use'], 'model.type': ['fopdt', 'integrating', 'heating', 'custom'],
    'process.kind': ['temperature', 'pressure', 'flow', 'level', 'speed', 'custom'],
    'model.source': ['parameters', 'csv', 'probe', 'manual'], 'history.time_unit': ['s', 'ms'],
    'controller.form': ['parallel', 'ideal'], 'controller.parameter_time_unit': ['s', 'min'],
    'controller.derivative_on': ['measurement', 'error'], 'controller.initialization': ['zero', 'tracking'],
    'controller.guardrail_policy': ['auto', 'relative'],
    'llm.provider': ['openai', 'anthropic', 'auto'],
    'llm.deepseek_thinking': ['disabled', 'enabled', 'provider_default'],
    'device.adapter': ['disabled', 'simulated', 'tcp', 'serial', 'custom'],
    'algorithms.include': ['ZN_PID', 'ZN_PI', 'SIMC_PI'],
    'legacy.WARM_START_METHOD': ['auto', 'zn', 'original-zn', 'none'],
    'legacy.UI_LANGUAGE': ['zh', 'en'],
    'legacy.HARDWARE_PROFILE': ['generic_serial_csv', 'stm32f407_openmv', 'mspm0_datavision'],
}
SECTION_LABELS = {
    'process': '被控量与单位',
    'model': '对象模型', 'history': '历史数据', 'identification': '辨识设置',
    'task': '目标任务', 'actuator': '执行器', 'controller': '控制器与护栏',
    'algorithms': '候选算法', 'evaluation': '评价要求', 'simulation': '仿真条件',
    'tuning': '调优与比较', 'llm': '大模型服务', 'device': '设备接入', 'output': '结果保存',
    'legacy': '原项目完整配置',
}
LABELS = {
    'K':'过程增益 K', 'tau_s':'时间常数 τ（秒）', 'theta_s':'纯滞后 θ（秒）',
    'type':'对象类型', 'source':'模型来源', 'initial_temperature_c':'初始温度（°C）',
    'target_temperature_c':'目标温度（°C）', 'ambient_temperature_c':'环境温度（°C）',
    'initial_heater_temperature_c':'加热器初温（°C）', 'initial_output':'初始输出',
    'operating_temperature_c':'工作点温度（°C）', 'operating_output':'工作点输出',
    'sample_time_s':'采样周期（秒）', 'form':'PID 参数形式', 'parameter_time_unit':'参数时间单位',
    'duration_s':'仿真时长（秒）', 'max_temperature_c':'允许最高温度（°C）',
    'max_tail_error_c':'末段温差上限（°C）', 'max_overshoot_pct':'超调上限（%）',
    'settling_band_c':'稳定带（±°C）', 'max_settling_time_s':'调节时间上限（秒）',
    'min':'下限', 'max':'上限', 'max_rate_per_s':'每秒最大输出变化',
    'max_increase_ratio':'最大增长倍数', 'guardrail_policy':'护栏策略',
    'p':'比例 Kp', 'i':'积分 Ki', 'd':'微分 Kd', 'file':'CSV 文件路径',
    'time_unit':'数据时间单位', 'time':'时间列', 'temperature':'温度列', 'output':'输出列',
    'rounds':'每组最大调优轮数', 'compare_original':'运行三组对照', 'include':'参与候选',
    'simc_lambda_s':'SIMC 响应时间 λ（秒）', 'enabled':'启用大模型',
    'provider':'API 协议', 'base_url':'服务地址', 'model':'模型名称',
    'credentials_file':'密钥文件', 'api_key_env':'密钥环境变量', 'timeout_s':'通信超时（秒）',
    'max_attempts':'最大请求次数', 'max_output_tokens':'回复 token 上限',
    'json_output':'请求 JSON 回复', 'deepseek_thinking':'DeepSeek 思考模式',
    'adapter':'设备接口', 'write_enabled':'允许参数写入', 'object_id':'控制回路标识',
    'host':'网关地址', 'port':'网关端口', 'serial_port':'串口', 'baud':'波特率',
    'custom_factory':'自定义工厂（模块:函数）', 'directory':'结果根目录', 'save_csv':'另存 CSV',
    'SERIAL_PORT':'原串口／DEMO', 'BAUD_RATE':'原串口波特率', 'HARDWARE_PROFILE':'原硬件协议配置',
    'BUFFER_SIZE':'每轮样本数', 'MAX_TUNING_ROUNDS':'原流程最大轮数',
    'MATLAB_MODEL_PATH':'Simulink 模型路径', 'MATLAB_ROOT':'MATLAB 安装目录',
    'MATLAB_PID_BLOCK_PATH':'主 PID 模块路径', 'MATLAB_PID_BLOCK_PATH_2':'第二 PID 模块路径',
    'MATLAB_PID_BLOCK_PATHS':'PID 模块路径列表', 'MATLAB_OUTPUT_SIGNAL':'受控变量信号',
    'MATLAB_OUTPUT_SIGNAL_CANDIDATES':'备选输出信号', 'MATLAB_CONTROL_SIGNAL':'控制输出信号',
    'MATLAB_SETPOINT_BLOCK':'设定值模块', 'MATLAB_SETPOINT':'原仿真目标值',
    'MATLAB_SIM_STEP_TIME':'每段仿真时间（秒）', 'CSV_EXPORT_PATH':'原流程 CSV 导出路径',
    'WARM_START_METHOD':'原 Python 仿真初始化', 'SIMC_LAMBDA':'原仿真 SIMC λ',
}


def legacy_defaults():
    from core.config import DEFAULT_CONFIG
    value = deepcopy(DEFAULT_CONFIG)
    for key in ('LLM_API_KEY', 'LLM_API_BASE_URL', 'LLM_MODEL_NAME', 'LLM_PROVIDER', 'LLM_REQUEST_TIMEOUT'):
        value.pop(key, None)
    value.update(WARM_START_METHOD='auto', SIMC_LAMBDA=None, UI_LANGUAGE='zh')
    for key in ('MATLAB_P_BLOCK_PATH', 'MATLAB_I_BLOCK_PATH', 'MATLAB_D_BLOCK_PATH',
                'MATLAB_P_BLOCK_PATH_2', 'MATLAB_I_BLOCK_PATH_2', 'MATLAB_D_BLOCK_PATH_2'):
        value.setdefault(key, '')
    return value


def field_metadata():
    fields = []
    def walk(obj, prefix=''):
        for key, value in obj.items():
            path = f'{prefix}.{key}' if prefix else key
            if isinstance(value, dict):
                walk(value, path)
                continue
            if path in ('schema_version', 'name'):
                continue
            kind = ('boolean' if isinstance(value, bool) else 'array' if isinstance(value, list)
                    else 'number' if value is None or isinstance(value, (int, float)) else 'string')
            label = LABELS.get(key, key)
            label = label.replace('温度', '被控量').replace('温差', '偏差').replace('（°C）','').replace('（℃）','')
            generic_labels = {'kind':'被控量类型','name':'被控量名称','unit':'单位',
                              'temperature':'被控量列名','time':'时间列名','output':'控制输出列名',
                              'min':'下限','max':'上限','p':'比例 P','i':'积分 I','d':'微分 D',
                              'max_increase_ratio':'单轮最大增幅倍数',
                              'K':'过程增益 K','tau_s':'时间常数 τ（秒）','theta_s':'纯滞后 θ（秒）'}
            label = generic_labels.get(key, label)
            label = label.replace('±°C', '±被控量单位')
            for missing, replacement in {
                'min_temperature_c':'允许最低被控量', 'tail_fraction':'末段评价比例',
                'min_settled_observation_s':'稳定后最少观察时间（秒）',
                'max_output_variation':'输出变化总量上限',
                'max_saturation_fraction':'输出饱和时间比例上限',
                'seed':'噪声随机种子', 'measurement_noise_std_c':'测量噪声标准差',
                'gain_scale':'仿真实际增益倍数', 'disturbance_time_s':'扰动开始时间（秒）',
                'disturbance_rate_c_per_s':'扰动变化率（被控量单位/秒）',
                'temperature_stop_min_c':'仿真停止下限', 'temperature_stop_max_c':'仿真停止上限',
                'samples_per_round':'发送给LLM的每轮样本数', 'stable_rounds':'连续稳定轮数',
                'average_error_threshold_c':'平均绝对偏差阈值',
                'monitor_min_temperature_c':'设备监测下限', 'monitor_max_temperature_c':'设备监测上限',
                'monitor_duration_s':'写入后监测时长（秒）', 'monitor_interval_s':'监测间隔（秒）',
                'restore_on_fault':'故障时尝试恢复旧参数', 'max_sample_age_s':'样本最大时龄（秒）',
                'max_planning_temperature_change_c':'规划期间允许被控量变化',
                'max_planning_output_change':'规划期间允许输出变化',
                'global_max_increase_ratio':'全局增幅倍数（0使用各项设置）',
                'derivative_filter_s':'微分滤波时间（秒）', 'derivative_on':'微分输入',
                'initialization':'积分初始化方式', 'probe_duration_s':'离线阶跃辨识时长（秒）',
                'probe_output_change':'离线阶跃输出变化量', 'max_relative_rmse':'最大辨识相对误差',
                'use_recorded_operating_point':'使用CSV中的实际工作点',
            }.items():
                if key==missing:label=replacement
            gains={'p':'比例P','i':'积分I','d':'微分D'}
            parts=path.split('.')
            if '.initial_pid.' in path:label='初始 PID · '+label
            if '.limits.' in path or '.PID_LIMITS.' in path:
                gain=next((gains[p] for p in parts if p in gains),'')
                label=gain+' · '+label
                if path.startswith('legacy.'):
                    scope=parts[2] if len(parts)>2 else ''
                    label={'default':'原流程通用','python_sim':'原Python','simulink':'原Simulink'}.get(scope,scope)+' · '+label
            description = COMMENTS.get(path, '原项目原有配置；与其配置模板和 Simulink 指南一致。')
            if not path.startswith('legacy.') and 'heater' not in path and 'ambient' not in path and 'cooling' not in path and 'heat_transfer' not in path:
                description = description.replace('°C/输出单位', '被控量单位/输出单位').replace('°C', '被控量单位').replace('温度', '被控量').replace('温差', '偏差')
            if path.startswith('process.'):
                description = '全流程使用这里选择的名称和单位，模型增益、任务、误差和设备数据必须使用同一单位；系统不自动换算。'
            if path == 'model.type':
                description = 'FOPDT：一阶惯性加滞后；integrating：积分加滞后（例如液位）；heating：专用摄氏加热模型；custom：按接口编写的 Python 模型。'
            if path == 'model.source':
                description = 'parameters：已知参数；csv：FOPDT阶跃辨识；probe：只在离线模型上辨识；manual：直接仿真用户初始 PID，可继续 LLM 优化，不套用辨识公式。'
            optional = path in ('actuator.max_rate_per_s','algorithms.simc_lambda_s',
                               'evaluation.max_settling_time_s','evaluation.max_temperature_c',
                               'evaluation.min_temperature_c','evaluation.max_output_variation',
                               'evaluation.max_saturation_fraction','simulation.disturbance_time_s')
            fields.append(dict(path=path, label=label, type=kind, default=value,
                               nullable=value is None or optional, choices=ENUMS.get(path),
                               description=description))
    walk(DEFAULTS)
    walk({'legacy': legacy_defaults()})
    return fields


class DesktopState:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for folder in ('uploads', 'jobs', 'profiles'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.project_file = self.root / 'project.json'
        self.legacy_file = self.root / 'legacy.json'
        if not self.project_file.exists():
            self._write(self.project_file, deepcopy(DEFAULTS))
        if not self.legacy_file.exists():
            self._write(self.legacy_file, legacy_defaults())

    @staticmethod
    def _write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        # Windows readers/antivirus may briefly deny replacement of an open file.
        # Retry the atomic rename; never fall back to truncating a live record.
        for attempt in range(32):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt==31:raise
                time.sleep(.01)

    def project(self):
        value = _merge(DEFAULTS, parse_commented_json(self.project_file.read_text(encoding='utf-8-sig')))
        validate(value)
        return value

    def legacy(self):
        value = legacy_defaults()
        value.update(json.loads(self.legacy_file.read_text(encoding='utf-8-sig')))
        value.pop('LLM_API_KEY', None)
        value.pop('API_KEY', None)
        return value

    def key_path(self, project=None):
        cfg = project or self.project()
        target = (self.root / (cfg['llm']['credentials_file'] or 'config.json')).resolve()
        if target in (self.project_file, self.legacy_file) or target.suffix.lower() != '.json':
            raise ValueError('密钥文件必须是独立 JSON，不能覆盖项目配置。')
        return target

    def api_key(self, project=None):
        cfg = project or self.project()
        key = os.environ.get(cfg['llm']['api_key_env'], '')
        if not key and self.key_path(cfg).is_file():
            data = json.loads(self.key_path(cfg).read_text(encoding='utf-8-sig'))
            key = data.get('LLM_API_KEY', data.get('API_KEY', ''))
        return key if isinstance(key, str) and key and not key.startswith('your-') else ''

    def snapshot(self):
        cfg=self.project()
        columns=[]
        history=(self.root/cfg['history']['file']).resolve()
        if cfg['model']['source']=='csv' and history.suffix.lower()=='.csv' and history.is_file():
            try:
                with history.open(encoding='utf-8-sig',newline='') as handle:
                    columns=next(csv.reader(handle),[])
            except (OSError,UnicodeError,csv.Error):pass
        return dict(project=self.project(), legacy=self.legacy(), key_configured=bool(self.api_key()),
                    fields=field_metadata(), sections=SECTION_LABELS, workspace=str(self.root),
                    history_columns=columns,
                    profiles=[p.stem for p in sorted((self.root / 'profiles').glob('*.json'))])

    def save(self, payload):
        cfg = _merge(DEFAULTS, payload['project'])
        validate(cfg)
        self.key_path(cfg)
        # Keep unknown legacy extensions for upstream prompt/transport compatibility.
        old = payload.get('legacy', self.legacy())
        if not isinstance(old, dict):
            raise ValueError('原项目配置必须是对象')
        old = deepcopy(old)
        old.pop('LLM_API_KEY', None)
        old.pop('API_KEY', None)
        self._write(self.project_file, cfg)
        self._write(self.legacy_file, old)
        if payload.get('clear_key'):
            self._write(self.key_path(cfg), {'LLM_API_KEY': ''})
        elif payload.get('api_key'):
            self._write(self.key_path(cfg), {'LLM_API_KEY': str(payload['api_key']).strip()})
        return self.snapshot()

    def upload_model(self, filename, raw, confirmed=False):
        if confirmed is not True:
            raise ValueError('Python模型会执行本地代码，请先确认文件来源可信。')
        if not filename.lower().endswith('.py') or len(raw) > 2*1024*1024:
            raise ValueError('请选择不超过2MB的Python模型文件')
        text = raw.decode('utf-8-sig')
        import ast
        ast.parse(text)
        name = re.sub(r'[^\w.\-]', '_', Path(filename.replace('\\','/')).name)
        target = self.root/'uploads'/(uuid.uuid4().hex[:10]+'_'+name)
        target.write_text(text, encoding='utf-8')
        return dict(path=str(target), factory=str(target)+':create_model')

    def upload_csv(self, filename, raw):
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError('CSV 超过20MB')
        if not filename.lower().endswith('.csv'):
            raise ValueError('请选择CSV文件')
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030')
        if '\x00' in text:
            raise ValueError('文件包含二进制内容')
        reader = csv.reader(io.StringIO(text))
        header = next(reader, [])
        if len(header) < 3 or len(set(header)) != len(header):
            raise ValueError('CSV至少需要三个不重复的列名')
        preview = [row for _, row in zip(range(6), reader)]
        name = re.sub(r'[^\w.\-]', '_', Path(filename.replace('\\','/')).name)
        target = self.root / 'uploads' / (uuid.uuid4().hex[:10] + '_' + name)
        target.write_text(text, encoding='utf-8')
        return dict(path=str(target), name=name, columns=header, preview=preview)

    def save_profile(self, name, payload):
        if not isinstance(name, str) or not re.fullmatch(r'[\w\-]{1,64}', name):
            raise ValueError('方案名称只能包含文字、数字、下划线或短横线')
        cfg = _merge(DEFAULTS, payload['project'])
        validate(cfg)
        old = deepcopy(payload.get('legacy', self.legacy()))
        old.pop('LLM_API_KEY', None)
        old.pop('API_KEY', None)
        self._write(self.root / 'profiles' / (name + '.json'), dict(project=cfg, legacy=old))

    def load_profile(self, name):
        if not re.fullmatch(r'[\w\-]{1,64}', name):
            raise ValueError('方案名称不正确')
        return json.loads((self.root / 'profiles' / (name + '.json')).read_text(encoding='utf-8'))
