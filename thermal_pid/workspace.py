"""User-selected local storage; copy before switching, never delete old records."""
import json
import os
from pathlib import Path
import shutil
import stat

from .config_comments import parse_commented_json
from .desktop_state import DesktopState


def selected_workspace(fallback, preferences):
    fallback, preferences = Path(fallback), Path(preferences)
    if preferences.is_file():
        value = json.loads(preferences.read_text(encoding='utf-8'))
        path = value.get('workspace')
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError('保存位置设置无效；可用 --workspace 指定数据目录。')
        return Path(path)
    return fallback


def _rewrite(value, source, destination):
    if isinstance(value, dict):
        return {k:_rewrite(v,source,destination) for k,v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v,source,destination) for v in value]
    if isinstance(value, str):
        # Only absolute references inside this workspace move; external models stay put.
        prefix=str(source)
        if os.path.normcase(value[:len(prefix)])==os.path.normcase(prefix) and (
            len(value)==len(prefix) or value[len(prefix)] in '/\\'):
            return str(destination)+value[len(prefix):]
    return value


def prepare_workspace(store, destination, copy_existing, preferences=None):
    if not isinstance(destination,str) or not destination.strip() or not Path(destination).is_absolute():
        raise ValueError('请选择专用数据文件夹，或填写完整的绝对路径。')
    if not isinstance(copy_existing,bool):raise ValueError('复制已有数据必须为布尔值。')
    source=store.root.resolve()
    target=Path(destination).resolve()
    if target==source:return store
    if target==Path(target.anchor):raise ValueError('请选择盘内的专用文件夹，不能直接使用盘符根目录。')
    if target in source.parents or source in target.parents:
        raise ValueError('新旧目录不能互相包含，请选择独立文件夹。')
    if target.exists() and not target.is_dir():raise ValueError('保存位置必须是文件夹。')
    if copy_existing and target.exists() and any(target.iterdir()):
        raise ValueError('复制旧数据需要一个空文件夹，避免覆盖已有记录；只切换目录时可取消复制。')
    if copy_existing:
        if source==Path(source.anchor):raise ValueError('源数据目录是盘符根目录，请改用专用文件夹。')
        # Refuse links/junctions instead of copying unrelated trees or losing linked data.
        for parent,dirs,files in os.walk(source,followlinks=False):
            for name in dirs+files:
                path=Path(parent)/name
                attributes=os.lstat(path)
                if path.is_symlink() or getattr(attributes,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
                    raise ValueError('旧数据目录包含链接或联接点，请先处理或选择不复制数据。')
    target.mkdir(parents=True,exist_ok=True)
    # Verify write access before any switch or remembered-location update.
    probe=target/('.write-check-'+__import__('uuid').uuid4().hex)
    try:
        probe.write_bytes(b'');probe.unlink()
        if copy_existing:
            for entry in source.iterdir():
                if entry.name in ('application.log','workspace-location.json','.workspace-location.json') or entry.name.endswith('.tmp'):
                    continue
                if entry.is_dir():shutil.copytree(entry,target/entry.name)
                else:shutil.copy2(entry,target/entry.name)
            for path in target.rglob('*.json'):
                try:values=parse_commented_json(path.read_text(encoding='utf-8-sig'))
                except (ValueError,UnicodeError):continue  # preserve unrelated/corrupt files verbatim
                migrated=_rewrite(values,source,target)
                if migrated!=values:DesktopState._write(path,migrated)
        new_store=DesktopState(target)
        new_store.snapshot()  # validate settings and key file before remembering or adopting
        if preferences:
            DesktopState._write(Path(preferences),dict(workspace=str(target)))
        return new_store
    except (OSError,ValueError) as exc:
        raise ValueError('切换保存位置未完成，旧目录和当前记录保留。请检查目录权限、剩余空间或配置；若复制中断，新目录可能有部分副本。') from exc
