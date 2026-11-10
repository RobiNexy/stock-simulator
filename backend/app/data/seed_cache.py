import shutil
from pathlib import Path


def seed_cache(seed_dir: Path, cache_dir: Path) -> int:
    """把镜像内的种子日线复制到可写缓存目录，已存在的文件不覆盖。"""
    if not seed_dir.is_dir():
        return 0
    cache_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in seed_dir.iterdir():
        if not source.is_file():
            continue
        target = cache_dir / source.name
        if target.exists():
            continue
        shutil.copy2(source, target)
        copied += 1
    return copied
