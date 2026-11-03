/* 位置路径工具（纯函数，零依赖）：
 * 把用户输入/后端 path 统一成「按分隔符切开的分量」，用于上传页判断
 * 「这个位置是已存在的行政区划」还是「需要新建」。
 * 分隔符与后端 app.py LOCATION_QUERY_SEP_PATTERN 保持一致：/ \ - — – > → ／ ＼ 空白。
 */
(function () {
    var SEP_RE = /[\/\\\-—–>→／＼\s]+/;

    function splitPath(value) {
        return String(value == null ? '' : value).split(SEP_RE).filter(function (part) {
            return part.length > 0;
        });
    }

    function normalizePath(value) {
        return splitPath(value).join('/');
    }

    function normalizeFlat(value) {
        return splitPath(value).join('');
    }

    function findPathMatches(results, typedPath) {
        var target = normalizeFlat(typedPath);
        if (!target) return [];
        var exact = [];
        var suffix = [];
        for (var i = 0; i < (results || []).length; i++) {
            var row = results[i];
            var flat = normalizeFlat(row.path || row.name);
            if (!flat) continue;
            if (flat === target) exact.push(row);
            else if (flat.length > target.length && flat.slice(-target.length) === target) suffix.push(row);
        }
        return exact.length ? exact : suffix;
    }

    // 唯一命中才返回（多条同名/同后缀由用户点选，避免误选）
    function findExactPathMatch(results, typedPath) {
        var matches = findPathMatches(results, typedPath);
        return matches.length === 1 ? matches[0] : null;
    }

    // 从「全路径」拆出已存在的最长前缀 + 缺失尾段（用于只暂存缺失的尾段）
    function splitExistingPrefix(results, typedPath) {
        var parts = splitPath(typedPath);
        if (parts.length < 2) return null;
        for (var count = parts.length - 1; count >= 1; count--) {
            var prefix = parts.slice(0, count).join('');
            for (var i = 0; i < (results || []).length; i++) {
                var flat = normalizeFlat(results[i].path || results[i].name);
                if (flat === prefix) {
                    return {existing: results[i], tail: parts.slice(count).join('/')};
                }
            }
        }
        return null;
    }

    var api = {
        splitPath: splitPath,
        normalizePath: normalizePath,
        normalizeFlat: normalizeFlat,
        findPathMatches: findPathMatches,
        findExactPathMatch: findExactPathMatch,
        splitExistingPrefix: splitExistingPrefix
    };
    window.foodLocationPath = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
