/* 极简 Markdown 子集渲染器（零依赖，先转义后渲染）。
 *
 * 支持：**粗体**、*斜体*、`行内代码`、```代码块```、[文本](链接)、换行。
 * 安全模型：
 *   1. 渲染前对整段原文做 HTML 转义，原始 HTML（<script>/<img onerror=...>）只会显示成文本；
 *   2. 只生成白名单标签：strong/em/code/pre/a/br，属性只有 a 的 href/target/rel；
 *   3. 链接协议白名单 http(s)/mailto，javascript:/data:/vbscript: 等不渲染为 <a>，退化为原文。
 * 因此可以在 innerHTML 里使用 renderMarkdown(comment)，但不要绕过它直接插原文。
 */
(function () {
    var ESCAPE_MAP = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return ESCAPE_MAP[ch];
        });
    }

    var SAFE_LINK = /^(?:https?:\/\/|mailto:)/i;

    function renderMarkdown(src) {
        // 控制字符（含占位符用的 \u0001）先清掉，避免与内部占位符冲突
        var text = escapeHtml(String(src == null ? '' : src).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, ''));
        var fences = [];
        var codes = [];

        text = text.replace(/```([\s\S]*?)```/g, function (match, code) {
            fences.push(code.replace(/^\n+|\n+$/g, ''));
            return '\u0001F' + (fences.length - 1) + '\u0001';
        });
        text = text.replace(/`([^`\n]+)`/g, function (match, code) {
            codes.push(code);
            return '\u0001C' + (codes.length - 1) + '\u0001';
        });
        text = text.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g, function (match, label, href) {
            var url = href.replace(/&amp;/g, '&');
            if (!SAFE_LINK.test(url) || /[\u0000-\u001f]/.test(href)) return match;
            return '<a href="' + href + '" target="_blank" rel="noopener noreferrer nofollow">' + label + '</a>';
        });
        text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
        text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        text = text.replace(/\r\n|\r|\n/g, '<br>');
        text = text.replace(/\u0001C(\d+)\u0001/g, function (match, index) {
            return '<code>' + codes[+index] + '</code>';
        });
        text = text.replace(/\u0001F(\d+)\u0001/g, function (match, index) {
            return '<pre><code>' + fences[+index] + '</code></pre>';
        });
        return text;
    }

    var api = {renderMarkdown: renderMarkdown, escapeHtml: escapeHtml};
    window.renderMarkdown = renderMarkdown;
    window.escapeHTML = window.escapeHTML || escapeHtml;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
