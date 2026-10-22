"""Run in ``hython`` by ``archive.rebuild.run``: re-make one workfile's caches.

    hython rebuild_worker.py REQUEST.json RESPONSE.json

REQUEST: ``{"hip": path, "caches": [{"node", "cache", "version"}, ...]}``.
Opens the hip once, then for each cache, in the order given: points the
``th::cache`` node at the recorded cache name and writes that version
(``CacheBase.cache(version_name=…)``). RESPONSE gets one
``{"node", "version", "ok", "error"}`` per cache; a failure is recorded and
the next cache still runs. The hip is never saved.
"""

import json
import sys
import traceback
from pathlib import Path


def main(request_path: str, response_path: str) -> int:
    import hou
    from tumblepipe.pipe.houdini import cache as cache_mod

    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    results = []
    try:
        hou.hipFile.load(request['hip'], suppress_save_prompt=True, ignore_load_warnings=True)
    except Exception as error:
        results = [
            dict(node=c['node'], version=c['version'], ok=False, error=f'cannot open the workfile: {error}')
            for c in request['caches']
        ]
    else:
        for cache in request['caches']:
            result = dict(node=cache['node'], version=cache['version'], ok=False, error=None)
            try:
                native = hou.node(cache['node'])
                if native is None:
                    raise RuntimeError('the node is not in this workfile')
                node = cache_mod.wrap(native)
                node.set_cache_name(cache['cache'])
                node.cache(version_name=cache['version'])
                result['ok'] = True
            except Exception as error:
                result['error'] = f'{type(error).__name__}: {error}'
                traceback.print_exc()
            results.append(result)
    Path(response_path).write_text(json.dumps(results), encoding='utf-8')
    return 0 if all(r['ok'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
