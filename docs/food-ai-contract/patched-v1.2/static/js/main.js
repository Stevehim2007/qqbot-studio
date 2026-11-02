(function () {
    let selectedLocationId = null;
    let currentRestaurantData = [];
    let activeTagFilter = [];
    let allTags = [];

    async function fetchJSON(url) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
    }

    function escapeHTML(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return (str || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
    }

    function buildTreeItems(nodes) {
        if (!nodes || nodes.length === 0) return '';
        let html = '';
        for (const node of nodes) {
            const hasChildren = node.has_children;
            html += '<li class="tree-node">';
            html += '<div class="tree-node-header" data-location-id="' + node.id + '">';
            html += '<span class="tree-toggle collapsed ' + (hasChildren ? '' : 'leaf') + '">▶</span>';
            html += '<span class="tree-label">' + escapeHTML(node.name) + '</span>';
            html += '</div>';
            if (hasChildren) {
                html += '<ul class="tree-children collapsed" data-loaded="0"></ul>';
            }
            html += '</li>';
        }
        return html;
    }

    function addTreeEvents() {
        var tree = document.getElementById('locationTree');
        tree.addEventListener('click', async function (e) {
            var header = e.target.closest('.tree-node-header');
            if (!header) return;
            var toggle = header.querySelector('.tree-toggle');
            if (e.target === toggle) {
                var childrenUl = header.nextElementSibling;
                if (!childrenUl || !childrenUl.classList.contains('tree-children')) return;
                var isCollapsed = childrenUl.classList.contains('collapsed');
                if (!isCollapsed) {
                    childrenUl.classList.add('collapsed');
                    toggle.classList.remove('expanded');
                    toggle.classList.add('collapsed');
                    return;
                }
                if (childrenUl.dataset.loaded === '1') {
                    childrenUl.classList.remove('collapsed');
                    toggle.classList.add('expanded');
                    toggle.classList.remove('collapsed');
                    return;
                }
                var locId = header.dataset.locationId;
                toggle.textContent = '…';
                try {
                    var children = await fetchJSON('/api/locations?parent_id=' + locId);
                    childrenUl.innerHTML = buildTreeItems(children);
                    childrenUl.dataset.loaded = '1';
                    childrenUl.classList.remove('collapsed');
                    toggle.classList.add('expanded');
                    toggle.classList.remove('collapsed');
                } catch (err) {
                    showToastMsg('加载子位置失败', 'error');
                }
                toggle.textContent = '▶';
            } else {
                var locId2 = header.dataset.locationId;
                if (locId2) {
                    selectLocation(parseInt(locId2), header.querySelector('.tree-label').textContent);
                }
            }
        });
    }

    function selectLocation(id, name) {
        selectedLocationId = id;
        document.querySelectorAll('.tree-node-header').forEach(function (h) { h.classList.remove('active'); });
        // 搜索结果的位置可能还没在树里渲染（树是懒加载的）；拿不到节点就跳过高亮，
        // 不能让 querySelector 返回 null 抛错，否则后面的视图切换/加载全都不执行。
        var node = document.querySelector('.tree-node-header[data-location-id="' + id + '"]');
        if (node) node.classList.add('active');
        document.getElementById('welcomeScreen').style.display = 'none';
        document.getElementById('restaurantView').style.display = 'block';
        document.getElementById('currentLocation').textContent = name;
        loadRestaurants(id);
        closeSidebar();
    }

    // ── Sidebar drawer (mobile) ─────────────────────────────────────────────

    function closeSidebar() {
        var sidebar = document.getElementById('sidebar');
        var overlay = document.getElementById('sidebarOverlay');
        if (sidebar) sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('show');
    }

    function setupMobileSidebar() {
        var hamburger = document.getElementById('hamburgerBtn');
        var sidebar = document.getElementById('sidebar');
        var overlay = document.getElementById('sidebarOverlay');

        if (!hamburger || !sidebar) return;

        hamburger.addEventListener('click', function (e) {
            e.stopPropagation();
            var isOpen = sidebar.classList.contains('open');
            if (isOpen) {
                closeSidebar();
            } else {
                sidebar.classList.add('open');
                if (overlay) overlay.classList.add('show');
            }
        });

        if (overlay) {
            overlay.addEventListener('click', function () {
                closeSidebar();
            });
        }
    }

    // ── Restaurant list ─────────────────────────────────────────────────────

    async function loadRestaurants(locationId) {
        var grid = document.getElementById('restaurantGrid');
        grid.innerHTML = '<div class="loading">加载中...</div>';

        var url = '/api/restaurants';
        var params = [];
        if (locationId) params.push('location_id=' + locationId);
        if (activeTagFilter.length > 0) params.push('tag_ids=' + activeTagFilter.join(','));
        var searchQ = document.getElementById('searchInput').value.trim();
        if (searchQ) {
            params.push('q=' + encodeURIComponent(searchQ));
            var searchType = document.getElementById('searchType').value;
            if (searchType) params.push('search_type=' + searchType);
        }
        if (params.length > 0) url += '?' + params.join('&');

        try {
            var data = await fetchJSON(url);
            currentRestaurantData = data;
            document.getElementById('restaurantCount').textContent = '共 ' + data.length + ' 家';
            renderSortedGrid();
        } catch (err) {
            grid.innerHTML = '<div class="loading">加载失败: ' + err.message + '</div>';
        }
    }

    function getSortValue(r, sortType) {
        if (sortType === 'name') return r.name.toLowerCase();
        if (sortType === 'score-desc') return -(r.total_avg || -1);
        if (sortType === 'score-asc') return (r.total_avg || 999);
        return null;
    }

    function renderSortedGrid() {
        var grid = document.getElementById('restaurantGrid');
        var sortType = document.getElementById('sortSelect').value;
        var data = currentRestaurantData.slice();

        if (sortType !== 'default') {
            if (sortType === 'name') {
                data.sort(function (a, b) {
                    return a.name.localeCompare(b.name, 'zh-CN');
                });
            } else {
                data.sort(function (a, b) {
                    var va = getSortValue(a, sortType);
                    var vb = getSortValue(b, sortType);
                    if (va < vb) return -1;
                    if (va > vb) return 1;
                    return 0;
                });
            }
        }

        if (data.length === 0) {
            grid.innerHTML = '<div class="empty-state">该区域暂无饭店数据</div>';
            return;
        }
        grid.innerHTML = data.map(renderCard).join('');
        bindReviewToggles();
    }

    function renderCard(r) {
        var tagsHTML = '';
        if (r.is_controversial) tagsHTML += '<span class="tag tag-controversial" title="' + escapeAttr((r.controversy_detail || []).join('\n')) + '" onclick="event.stopPropagation();window._showTagInfo(this)">有争议</span>';
        if (r.is_expired) tagsHTML += '<span class="tag tag-expired" title="' + escapeAttr(r.expiry_detail || '') + '" onclick="event.stopPropagation();window._showTagInfo(this)">已过期</span>';

        var userTagsHTML = '';
        if (r.tags && r.tags.length > 0) {
            var collapsed = r.tags.length > 3;
            var visibleTags = collapsed ? r.tags.slice(0, 3) : r.tags;
            var hiddenTags = collapsed ? r.tags.slice(3) : [];
            var rid = r.id;
            userTagsHTML = '<div class="card-tags" data-restaurant-id="' + rid + '">' +
                visibleTags.map(function (t) { return '<span class="tag-badge-sm">' + escapeHTML(t) + '</span>'; }).join('') +
                (hiddenTags.length > 0 ? '<span class="tag-badge-sm tag-more" data-rid="' + rid + '" onclick="event.stopPropagation();window._toggleTags(this)">...展开</span><span class="tags-hidden" id="tags-hidden-' + rid + '" style="display:none">' + hiddenTags.map(function (t) { return '<span class="tag-badge-sm">' + escapeHTML(t) + '</span>'; }).join('') + '</span>' : '') +
                '</div>';
        }

        var borderClass = '';
        if (r.is_controversial) borderClass += ' has-controversial';
        if (r.is_expired && !r.is_controversial) borderClass += ' has-expired';

        var scoresHTML = '';
        if (r.taste_avg != null || r.value_avg != null) {
            var parts = [];
            if (r.taste_avg != null) parts.push('<span class="score-item"><span class="score-label">味道</span> <span class="score-value">' + r.taste_avg + ' / 5</span></span>');
            if (r.value_avg != null) parts.push('<span class="score-item"><span class="score-label">性价比</span> <span class="score-value">' + r.value_avg + ' / 5</span></span>');
            if (r.total_avg != null) parts.push('<span class="score-item"><span class="score-label">总分</span> <span class="score-value score-total">' + r.total_avg + ' / 10</span></span>');
            scoresHTML = '<div class="card-scores">' + parts.join('') + '</div>';
        }

        var reviewsHTML = '';
        if (r.reviews && r.reviews.length > 0) {
            var items = r.reviews.map(function (rev) {
                var revScores = '';
                var hasScore = rev.taste_score != null || rev.value_score != null;
                if (hasScore) {
                    var p = [];
                    if (rev.taste_score != null) p.push('味道 ' + rev.taste_score);
                    if (rev.value_score != null) p.push('性价比 ' + rev.value_score);
                    revScores = '<div class="review-scores">' + p.join(' + ') + '</div>';
                }
                return '<div class="review-item">' +
                    '<div class="review-item-body">' +
                    revScores +
                    '<p>' + escapeHTML(rev.comment || '') + '</p>' +
                    '<div class="review-meta"><span>' + escapeHTML(rev.username) + '</span><span>' + rev.date + '</span></div>' +
                    '</div>' +
                    '<button class="review-edit-btn" data-review-id="' + rev.id +
                    '" data-taste="' + (rev.taste_score || '') + '" data-value="' + (rev.value_score || '') +
                    '" data-comment="' + escapeHTML(rev.comment || '') + '">修改</button>' +
                    '</div>';
            }).join('');
            reviewsHTML = '<div class="review-toggle" data-restaurant-id="' + r.id + '">查看评价 (' + r.reviews.length + '条) ▼</div>' +
                '<div class="review-list collapsed">' + items + '</div>';
        }

        return '<div class="restaurant-card' + borderClass + '">' +
            '<div class="card-header"><span class="card-name">' + escapeHTML(r.name) + '</span>' +
            (r.location_name ? '<span class="card-location">' + escapeHTML(r.location_name) + '</span>' : '') +
            tagsHTML + userTagsHTML + '<button class="review-edit-btn" style="margin-left:8px" onclick="event.stopPropagation();window._correctLocation(' + r.id + ')">纠错位置</button></div>' + scoresHTML + reviewsHTML + '</div>';
    }

    function bindReviewToggles() {
        document.querySelectorAll('.review-toggle').forEach(function (t) {
            t.addEventListener('click', function () {
                var list = this.nextElementSibling;
                if (list && list.classList.contains('review-list')) {
                    list.classList.toggle('collapsed');
                    this.textContent = this.textContent.replace('▼', '▲').replace('▲', '▼');
                }
            });
        });
        bindEditButtons();
    }

    function bindEditButtons() {
        document.querySelectorAll('.review-edit-btn').forEach(function (btn) {
            if (!btn.dataset.reviewId) return;
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                var id = this.dataset.reviewId;
                var taste = this.dataset.taste || '';
                var value = this.dataset.value || '';
                var comment = this.getAttribute('data-comment') || '';
                openEditModal(id, taste, value, comment);
            });
        });
    }

    function openEditModal(id, taste, value, comment) {
        var overlay = document.getElementById('modalOverlay');
        var content = document.getElementById('modalContent');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'modalOverlay';
            overlay.className = 'modal-overlay';
            content = document.createElement('div');
            content.id = 'modalContent';
            content.className = 'modal-box';
            overlay.appendChild(content);
            document.body.appendChild(overlay);
        }

        var body = '<div class="form-group"><label>密钥（必须输入才能修改）</label>' +
            '<input type="text" id="editPassword" placeholder="输入密钥" pattern="[a-zA-Z0-9]+"></div>' +
            '<div class="form-row"><div class="form-group half"><label>味道分</label>' +
            '<input type="number" id="editTaste" min="0.1" max="5" step="0.0001" value="' + taste + '"></div>' +
            '<div class="form-group half"><label>性价比分</label>' +
            '<input type="number" id="editValue" min="0.1" max="5" step="0.0001" value="' + value + '"></div></div>' +
            '<div class="form-group"><label>评价</label>' +
            '<textarea id="editComment" rows="4">' + comment + '</textarea></div>' +
            '<div class="modal-actions"><button class="btn-cancel" id="modalCancel">取消</button>' +
            '<button class="btn-save" id="modalSave">保存修改</button></div>' +
            '<div class="login-error" id="editError"></div>';

        content.innerHTML = '<h3>修改评价</h3>' + body;
        overlay.style.display = 'flex';

        document.getElementById('modalCancel').addEventListener('click', function () {
            overlay.style.display = 'none';
        });
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay) overlay.style.display = 'none';
        });

        document.getElementById('modalSave').addEventListener('click', async function () {
            var pw = document.getElementById('editPassword').value.trim();
            var err = document.getElementById('editError');
            if (!pw) { err.textContent = '请输入密钥'; return; }
            if (!/^[a-zA-Z0-9]+$/.test(pw)) { err.textContent = '密钥只能包含数字和大小写字母'; return; }

            var body = {
                password: pw,
                taste_score: document.getElementById('editTaste').value,
                value_score: document.getElementById('editValue').value,
                comment: document.getElementById('editComment').value.trim()
            };

            try {
                var resp = await fetch('/api/reviews/' + id, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                var data = await resp.json();
                if (!resp.ok) { err.textContent = data.error || '修改失败'; return; }
                overlay.style.display = 'none';
                if (selectedLocationId != null) loadRestaurants(selectedLocationId);
                showToastMsg('评价已修改', 'success');
            } catch (ex) { err.textContent = '网络错误'; }
        });
    }

    function showToastMsg(msg, type) {
        var t = document.getElementById('toast') || document.createElement('div');
        if (!t.id) {
            t.id = 'toast';
            t.className = 'toast';
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.className = 'toast ' + type + ' show';
        setTimeout(function () { t.className = 'toast'; }, 3000);
    }

    var tagFilterLoaded = false;

    async function loadAllTags() {
        if (tagFilterLoaded) return;
        try {
            allTags = await fetchJSON('/api/tags');
            tagFilterLoaded = true;
        } catch (err) { /* silent */ }
    }

    function renderTagFilterList() {
        var list = document.getElementById('tagFilterList');
        var q = (document.getElementById('tagSearchInput').value || '').toLowerCase();
        list.innerHTML = '';
        allTags.forEach(function (t) {
            if (q && t.name.toLowerCase().indexOf(q) === -1) return;
            var el = document.createElement('span');
            el.className = 'tag-filter-tag';
            if (activeTagFilter.indexOf(t.id) !== -1) el.classList.add('selected');
            el.textContent = t.name;
            el.addEventListener('click', function () {
                var idx = activeTagFilter.indexOf(t.id);
                if (idx === -1) { activeTagFilter.push(t.id); } else { activeTagFilter.splice(idx, 1); }
                updateTagFilterUI();
            });
            list.appendChild(el);
        });
        updateTagFilterUI();
    }

    function updateTagFilterUI() {
        var selectedDiv = document.getElementById('tagFilterSelected');
        var countSpan = document.getElementById('tagFilterCount');
        selectedDiv.innerHTML = '';
        selectedDiv.style.display = activeTagFilter.length ? 'flex' : 'none';
        activeTagFilter.forEach(function (tid) {
            var tag = allTags.find(function (t) { return t.id === tid; });
            if (tag) {
                var span = document.createElement('span');
                span.className = 'tag-badge-sm tag-filter-selected-tag';
                span.textContent = tag.name + ' ×';
                span.style.cursor = 'pointer';
                span.addEventListener('click', function () {
                    var idx = activeTagFilter.indexOf(tid);
                    if (idx !== -1) activeTagFilter.splice(idx, 1);
                    renderTagFilterList();
                });
                selectedDiv.appendChild(span);
            }
        });
        countSpan.textContent = activeTagFilter.length ? '已选 ' + activeTagFilter.length + ' 个' : '';
        document.getElementById('tagFilterClearAll').style.display = activeTagFilter.length ? '' : 'none';
        // update visual selection on grid
        document.querySelectorAll('.tag-filter-tag').forEach(function (el) {
            var n = el.textContent;
            var t = allTags.find(function (t2) { return t2.name === n; });
            if (t) el.classList.toggle('selected', activeTagFilter.indexOf(t.id) !== -1);
        });
    }

    function setupTagFilter() {
        var btn = document.getElementById('tagFilterBtn');
        var overlay = document.getElementById('tagFilterOverlay');
        var applyBtn = document.getElementById('tagFilterApply');
        var cancelBtn = document.getElementById('tagFilterCancel');
        var search = document.getElementById('tagSearchInput');
        var savedFilter = activeTagFilter.slice();

        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            savedFilter = activeTagFilter.slice();
            overlay.style.display = 'flex';
            loadAllTags().then(function () {
                search.value = '';
                renderTagFilterList();
            });
        });

        search.addEventListener('input', function () { renderTagFilterList(); });

        applyBtn.addEventListener('click', function () {
            savedFilter = activeTagFilter.slice();
            overlay.style.display = 'none';
            if (selectedLocationId != null) loadRestaurants(selectedLocationId);
        });

        cancelBtn.addEventListener('click', function () {
            activeTagFilter = savedFilter.slice();
            renderTagFilterList();
            overlay.style.display = 'none';
        });
        overlay.addEventListener('click', function (e) { if (e.target === overlay) { activeTagFilter = savedFilter.slice(); renderTagFilterList(); overlay.style.display = 'none'; } });

        document.getElementById('tagFilterClearAll').addEventListener('click', function () {
            activeTagFilter = [];
            renderTagFilterList();
        });
    }

    async function init() {
        addTreeEvents();
        setupMobileSidebar();
        setupTagFilter();
        document.getElementById('searchInput').addEventListener('input', function () {
            if (selectedLocationId != null || document.getElementById('restaurantView').style.display !== 'none') {
                loadRestaurants(selectedLocationId);
            }
        });
        document.getElementById('searchType').addEventListener('change', function () {
            if (selectedLocationId != null || document.getElementById('restaurantView').style.display !== 'none') {
                loadRestaurants(selectedLocationId);
            }
        });

        var tree = document.getElementById('locationTree');
        try {
            var locations = await fetchJSON('/api/locations');
            tree.innerHTML = '<ul class="tree-children">' + buildTreeItems(locations) + '</ul>';
        } catch (err) {
            tree.innerHTML = '<div class="tree-loading">加载位置失败</div>';
        }

        var sidebarSearch = document.getElementById('sidebarLocSearch');
        var sidebarResults = document.getElementById('sidebarLocResults');
        var sidebarSearchTimer = null;
        sidebarSearch.addEventListener('input', function () {
            clearTimeout(sidebarSearchTimer);
            var q = sidebarSearch.value.trim();
            if (!q) {
                sidebarResults.style.display = 'none';
                sidebarResults.innerHTML = '';
                tree.style.display = '';
                return;
            }
            sidebarSearchTimer = setTimeout(async function () {
                try {
                    var results = await fetchJSON('/api/locations/search?q=' + encodeURIComponent(q));
                    if (sidebarSearch.value.trim() !== q) return;
                    sidebarResults.innerHTML = results.length === 0 ?
                        '<li class="loc-search-empty">未找到匹配位置</li>' :
                        results.map(function (r) {
                            return '<li class="sidebar-search-item" data-loc-id="' + r.id + '" data-loc-name="' + escapeHTML(r.name).replace(/'/g, '&#39;') + '" data-loc-path="' + escapeHTML(r.path || '').replace(/'/g, '&#39;') + '">' +
                                '<span class="ssi-name">' + escapeHTML(r.name) + '</span>' +
                                '<span class="ssi-path">' + escapeHTML(r.path || '') + '</span>' +
                                '</li>';
                        }).join('');
                    sidebarResults.style.display = 'block';
                    tree.style.display = 'none';
                } catch (err) { /* silent */ }
            }, 250);
        });
        sidebarResults.addEventListener('click', function (e) {
            var item = e.target.closest('.sidebar-search-item');
            if (!item || !item.dataset.locId) return;
            selectLocation(parseInt(item.dataset.locId), item.dataset.locPath || item.dataset.locName);
            sidebarSearch.value = '';
            sidebarResults.style.display = 'none';
            sidebarResults.innerHTML = '';
            tree.style.display = '';
        });

        try {
            var allRests = await fetchJSON('/api/restaurants');
            var areaIds = {};
            for (var i = 0; i < allRests.length; i++) {
                if (allRests[i].location_id) areaIds[allRests[i].location_id] = true;
            }
            document.getElementById('welcomeStats').innerHTML =
                '<span>收录 ' + allRests.length + ' 家饭店</span>' +
                '<span>来自 ' + Object.keys(areaIds).length + ' 个区域</span>';
        } catch (e) {}

        document.getElementById('sortSelect').addEventListener('change', function () {
            if (currentRestaurantData.length > 0) renderSortedGrid();
        });
    }

    window._showTagInfo = function (el) {
        showToastMsg(el.title || '暂无详细信息', 'info');
    };

    window._toggleTags = function (el) {
        var rid = el.getAttribute('data-rid');
        var hidden = document.getElementById('tags-hidden-' + rid);
        hidden.style.display = 'contents';
        el.style.display = 'none';
        var card = document.querySelector('.card-tags[data-restaurant-id="' + rid + '"]');
        if (card) {
            card.addEventListener('click', function collapse(e) {
                hidden.style.display = 'none';
                el.style.display = '';
                card.removeEventListener('click', collapse);
            }, { once: true });
        }
    };

    init();

    window._correctLocation = function (restaurantId) {
        var overlay = document.getElementById('modalOverlay');
        var content = document.getElementById('modalContent');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'modalOverlay';
            overlay.className = 'modal-overlay';
            content = document.createElement('div');
            content.id = 'modalContent';
            content.className = 'modal-box';
            overlay.appendChild(content);
            document.body.appendChild(overlay);
        }

        var locTree = [];
        var stagedCorrection = null;

        fetchJSON('/api/locations').then(function (tree) { locTree = tree; });

        content.innerHTML = '<h3>纠错位置</h3>' +
            '<div class="form-group">' +
            '<label>选择正确位置</label>' +
            '<div class="select-row">' +
            '<div class="tree-select-wrapper loc-select-wrapper">' +
            '<div class="tree-trigger" id="correctLocTrigger">' +
            '<span class="tree-trigger-text" id="correctLocTriggerText">点击选择位置区域</span>' +
            '<span class="tree-trigger-arrow">▼</span>' +
            '</div>' +
            '<div class="tree-dropdown" id="correctTreeDropdown"></div>' +
            '</div>' +
            '<button type="button" class="btn-sm" id="correctNewLocToggle">+ 新建位置</button>' +
            '</div>' +
            '<input type="hidden" id="correctLocId" value="">' +
            '<div class="new-location-row" id="correctNewLocRow" style="display:none;margin-top:8px">' +
            '<input type="text" id="correctNewLocName" placeholder="新位置名称（多层用 / 或 - 分隔）">' +
            '<div class="tree-select-wrapper">' +
            '<div class="tree-trigger" id="correctNewLocParentTrigger">' +
            '<span class="tree-trigger-text">点击选择父级位置</span>' +
            '<span class="tree-trigger-arrow">▼</span>' +
            '</div>' +
            '<div class="tree-dropdown" id="correctNewLocParentDropdown"></div>' +
            '</div>' +
            '<input type="hidden" id="correctNewLocParentId" value="">' +
            '<button type="button" class="btn-sm btn-primary" id="correctStageBtn">暂存</button>' +
            '</div>' +
            '</div>' +
            '<div class="form-group">' +
            '<label>提交人</label>' +
            '<input type="text" id="correctSubmitter" placeholder="你的称呼（可选）">' +
            '</div>' +
            '<div class="modal-actions">' +
            '<button class="btn-cancel" id="correctCancel">取消</button>' +
            '<button class="btn-save" id="correctSubmit">提交纠错</button>' +
            '</div>' +
            '<div class="login-error" id="correctError"></div>';
        overlay.style.display = 'flex';

        var trigger = document.getElementById('correctLocTrigger');
        var dropdown = document.getElementById('correctTreeDropdown');
        var hiddenId = document.getElementById('correctLocId');
        var triggerText = document.getElementById('correctLocTriggerText');

        function buildNode(nodes, depth) {
            depth = depth || 0;
            var html = '<ul class="tree-select-list">';
            for (var i = 0; i < nodes.length; i++) {
                var n = nodes[i];
                var hasKids = n.has_children;
                html += '<li class="tree-select-node">';
                html += '<div class="tree-select-item" data-loc-id="' + n.id + '" data-loc-name="' + escapeHTML(n.name).replace(/'/g, '&#39;') + '" style="padding-left:' + (12 + depth * 18) + 'px">';
                html += '<span class="tree-select-toggle">' + (hasKids ? '▶' : '') + '</span>';
                html += '<span class="tree-select-name">' + escapeHTML(n.name) + '</span>';
                html += '</div>';
                if (hasKids) html += '<div class="tree-select-children" data-depth="' + depth + '" data-loaded="0" style="display:none"></div>';
                html += '</li>';
            }
            html += '</ul>';
            return html;
        }

        function bindTree(dd, onSelect) {
            if (dd.dataset.bound === '1') return;
            dd.dataset.bound = '1';
            dd.addEventListener('click', async function (ev) {
                var item = ev.target.closest('.tree-select-item');
                if (!item) return;
                ev.stopPropagation();
                var toggle = item.querySelector('.tree-select-toggle');
                if (ev.target === toggle) {
                    var c = item.nextElementSibling;
                    if (!c || !c.classList.contains('tree-select-children')) return;
                    if (c.style.display !== 'none') {
                        c.style.display = 'none';
                        toggle.textContent = '▶';
                        return;
                    }
                    if (c.dataset.loaded !== '1') {
                        toggle.textContent = '…';
                        try {
                            var kids = await fetchJSON('/api/locations?parent_id=' + item.dataset.locId);
                            c.innerHTML = buildNode(kids, parseInt(c.dataset.depth) + 1);
                            c.dataset.loaded = '1';
                        } catch (err) {
                            toggle.textContent = '▶';
                            return;
                        }
                    }
                    c.style.display = 'block';
                    toggle.textContent = '▼';
                } else {
                    onSelect(parseInt(item.dataset.locId), item.dataset.locName);
                }
            });

            var searchTimer = null;
            dd.addEventListener('input', function (ev) {
                var input = ev.target;
                if (!input.classList.contains('loc-search-input')) return;
                clearTimeout(searchTimer);
                var q = input.value.trim();
                var body = dd.querySelector('.tree-dd-body');
                if (!body) return;
                if (!q) {
                    body.innerHTML = buildNode(locTree || [], 0);
                    return;
                }
                searchTimer = setTimeout(async function () {
                    try {
                        var results = await fetchJSON('/api/locations/search?q=' + encodeURIComponent(q));
                        if (input.value.trim() !== q) return;
                        body.innerHTML = results.length === 0 ?
                            '<div class="loc-search-empty">未找到匹配位置</div>' :
                            '<ul class="tree-select-list">' + results.map(function (r) {
                                return '<li class="tree-select-node"><div class="tree-select-item loc-search-item" data-loc-id="' + r.id + '" data-loc-name="' + escapeHTML(r.name).replace(/'/g, '&#39;') + '">' +
                                    '<span class="tree-select-toggle" style="visibility:hidden">▶</span>' +
                                    '<span class="tree-select-name">' + escapeHTML(r.name) + '</span>' +
                                    '<span class="loc-search-item-path">' + escapeHTML(r.path || '') + '</span>' +
                                    '</div></li>';
                            }).join('') + '</ul>';
                    } catch (err) { /* silent */ }
                }, 250);
            });
        }

        function dropdownHTML(tree) {
            return '<input type="text" class="loc-search-input" placeholder="搜索位置，快速定位...">' +
                '<div class="tree-dd-body">' + buildNode(tree || [], 0) + '</div>';
        }

        trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            if (dropdown.style.display === 'block') { dropdown.style.display = 'none'; return; }
            dropdown.innerHTML = dropdownHTML(locTree);
            bindTree(dropdown, function (id, name) {
                hiddenId.value = id;
                triggerText.textContent = name;
                trigger.classList.add('has-selection');
                dropdown.style.display = 'none';
                stagedCorrection = null;
            });
            dropdown.style.display = 'block';
        });

        document.addEventListener('click', function (e) { if (!trigger.contains(e.target) && !dropdown.contains(e.target)) dropdown.style.display = 'none'; });

        document.getElementById('correctNewLocToggle').addEventListener('click', function () {
            var row = document.getElementById('correctNewLocRow');
            row.style.display = row.style.display === 'none' ? 'flex' : 'none';
            if (row.style.display === 'flex') {
                var pDropdown = document.getElementById('correctNewLocParentDropdown');
                var pTrigger = document.getElementById('correctNewLocParentTrigger');
                pDropdown.innerHTML = dropdownHTML(locTree);
                bindTree(pDropdown, function (id, name) {
                    document.getElementById('correctNewLocParentId').value = id;
                    pTrigger.querySelector('.tree-trigger-text').textContent = '已选择: ' + name;
                    pTrigger.classList.add('has-selection');
                });
                pTrigger.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    if (pDropdown.style.display === 'block') { pDropdown.style.display = 'none'; return; }
                    pDropdown.innerHTML = dropdownHTML(locTree);
                    bindTree(pDropdown, function (id2, name2) {
                        document.getElementById('correctNewLocParentId').value = id2;
                        pTrigger.querySelector('.tree-trigger-text').textContent = '已选择: ' + name2;
                        pTrigger.classList.add('has-selection');
                    });
                    pDropdown.style.display = 'block';
                });
            }
        });

        document.getElementById('correctStageBtn').addEventListener('click', function () {
            var n = document.getElementById('correctNewLocName').value.trim();
            if (!n) { document.getElementById('correctError').textContent = '请输入位置名称'; return; }
            stagedCorrection = { name: n, parent_id: document.getElementById('correctNewLocParentId').value || null };
            triggerText.textContent = n + ' (暂存)';
            trigger.classList.add('has-selection');
            hiddenId.value = '';
            document.getElementById('correctNewLocRow').style.display = 'none';
        });

        document.getElementById('correctCancel').addEventListener('click', function () { overlay.style.display = 'none'; });
        overlay.addEventListener('click', function (e) { if (e.target === overlay) overlay.style.display = 'none'; });

        document.getElementById('correctSubmit').addEventListener('click', async function () {
            var err = document.getElementById('correctError');
            var locId = hiddenId.value;
            if (!locId && !stagedCorrection) { err.textContent = '请选择或新建位置'; return; }
            try {
                var body = { restaurant_id: restaurantId, submitter: document.getElementById('correctSubmitter').value.trim() || '匿名' };
                if (stagedCorrection) {
                    body.new_location_name = stagedCorrection.name;
                    body.new_location_parent_id = stagedCorrection.parent_id;
                } else {
                    body.new_location_id = parseInt(locId);
                }
                var resp = await fetch('/api/corrections', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                var data = await resp.json();
                if (!resp.ok) { err.textContent = data.error || '提交失败'; return; }
                overlay.style.display = 'none';
                showToastMsg('纠错已提交，等待管理员审批', 'success');
            } catch (ex) { err.textContent = '网络错误'; }
        });
    };
})();
