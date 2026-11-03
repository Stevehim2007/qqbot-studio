(function () {
    var allData = null;
    var currentLocationFilter = null;
    var currentLocationFilterName = '';
    var locationFilterIds = null;
    var expandedLocIds = {};
    var loadedLocChildren = {};

    async function fetchJSON(url, options) {
        var resp = await fetch(url, options);
        var body = await resp.text();
        if (!resp.ok) {
            var errMsg = body;
            try {
                var err = JSON.parse(body);
                errMsg = err.error || body;
            } catch (e) {}
            throw new Error(errMsg);
        }
        return JSON.parse(body);
    }

    function showToast(msg, type) {
        var t = document.getElementById('toast');
        t.textContent = msg; t.className = 'toast ' + type + ' show';
        setTimeout(function () { t.className = 'toast'; }, 3000);
    }

    function escapeHTML(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // markdown.js 未加载（旧缓存 / 分步部署）时退化为纯文本 + 换行
    var renderMarkdown = window.renderMarkdown || function (src) {
        return escapeHTML(src == null ? '' : src).replace(/\r\n|\r|\n/g, '<br>');
    };

    function escapeJS(str) {
        return escapeHTML(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\r/g, '').replace(/\n/g, ' ');
    }

    async function fetchLocationChildren(locId) {
        if (loadedLocChildren[locId]) return loadedLocChildren[locId];
        var kids = await fetchJSON('/api/locations?parent_id=' + locId);
        loadedLocChildren[locId] = kids;
        return kids;
    }

    function buildAdminTreeOption(nodes, depth) {
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

    function bindPickerTree(dropdown, onPick) {
        if (dropdown.dataset.bound === '1') return;
        dropdown.dataset.bound = '1';
        dropdown.addEventListener('click', async function (ev) {
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
                        c.innerHTML = buildAdminTreeOption(kids, parseInt(c.dataset.depth) + 1);
                        c.dataset.loaded = '1';
                    } catch (err) {
                        toggle.textContent = '▶';
                        return;
                    }
                }
                c.style.display = 'block';
                toggle.textContent = '▼';
            } else {
                onPick(parseInt(item.dataset.locId), item.dataset.locName);
            }
        });

        var searchTimer = null;
        dropdown.addEventListener('input', function (ev) {
            var input = ev.target;
            if (!input.classList.contains('loc-search-input')) return;
            clearTimeout(searchTimer);
            var q = input.value.trim();
            var body = dropdown.querySelector('.tree-dd-body');
            if (!body) return;
            if (!q) {
                body.innerHTML = buildAdminTreeOption(allData.location_tree, 0);
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

    function setupLazyPicker(trigger, dropdown, onPick) {
        trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            if (dropdown.style.display === 'block') {
                dropdown.style.display = 'none';
            } else {
                dropdown.innerHTML = '<input type="text" class="loc-search-input" placeholder="搜索位置，快速定位...">' +
                    '<div class="tree-dd-body">' + buildAdminTreeOption(allData.location_tree, 0) + '</div>';
                bindPickerTree(dropdown, onPick);
                dropdown.style.display = 'block';
            }
        });
        document.addEventListener('click', function (e) {
            if (!trigger.contains(e.target) && !dropdown.contains(e.target)) {
                dropdown.style.display = 'none';
            }
        });
    }

    function setupAdminLocationTree() {
        var trigger = document.getElementById('adminNewLocParentTrigger');
        var dropdown = document.getElementById('adminTreeDropdown');
        var hidden = document.getElementById('newLocParent');

        if (!trigger || !dropdown || !hidden) return;
        if (trigger._treeSetupDone) return;
        trigger._treeSetupDone = true;

        setupLazyPicker(trigger, dropdown, function (id, name) {
            hidden.value = id;
            trigger.querySelector('.tree-trigger-text').textContent = '已选择: ' + name;
            trigger.classList.add('has-selection');
            dropdown.style.display = 'none';
        });
    }

    function openModal(title, bodyHTML, onSave) {
        var overlay = document.getElementById('modalOverlay');
        var content = document.getElementById('modalContent');
        content.innerHTML = '<h3>' + title + '</h3>' + bodyHTML +
            '<div class="modal-actions"><button class="btn-cancel" id="modalCancel">取消</button>' +
            '<button class="btn-save" id="modalSave">保存</button></div>';
        overlay.style.display = 'flex';
        document.getElementById('modalCancel').addEventListener('click', function () { overlay.style.display = 'none'; });
        document.getElementById('modalSave').addEventListener('click', async function () {
            try {
                await onSave();
                overlay.style.display = 'none';
                await loadAllData();
            } catch (err) { showToast(err.message, 'error'); }
        });
    }

    // ── Location tree ────────────────────────────────────────────────────────

    function renderLocationTree(nodes, depth) {
        depth = depth || 0;
        var html = '<ul class="admin-loc-children">';
        for (var i = 0; i < nodes.length; i++) {
            var node = nodes[i];
            var hasKids = node.has_children;
            var isActive = currentLocationFilter === node.id;
            html += '<li class="admin-loc-node">';
            html += '<div class="admin-loc-header">';
            if (hasKids) {
                html += '<span class="tree-toggle' + (expandedLocIds[node.id] ? ' expanded' : '') + '" data-tgl-id="' + node.id + '">▶</span>';
            } else {
                html += '<span class="tree-toggle leaf" style="visibility:hidden">▶</span>';
            }
            html += '<span class="admin-loc-name' + (isActive ? ' active' : '') + '" onclick="window._filterByLocation(' + node.id + ',\'' + escapeJS(node.name) + '\')">' + escapeHTML(node.name) + '<span class="loc-id">#' + node.id + '</span></span>';
            html += '<span class="admin-loc-actions">';
            html += '<button onclick="window._editLocation(' + node.id + ',\'' + escapeJS(node.name) + '\', ' + (node.parent_id || 'null') + ')">编辑</button>';
            html += '<button onclick="window._moveLocation(' + node.id + ',\'' + escapeJS(node.name) + '\', ' + (node.parent_id || 'null') + ')">移动</button>';
            html += '<button onclick="window._addChildLocation(' + node.id + ')">+子级</button>';
            html += '<button class="btn-danger" onclick="window._deleteLocation(' + node.id + ',\'' + escapeJS(node.name) + '\',' + (hasKids ? 1 : 0) + ')">删除</button>';
            html += '</span></div>';
            if (hasKids) {
                var loaded = !!loadedLocChildren[node.id];
                var expanded = !!expandedLocIds[node.id];
                html += '<ul class="admin-loc-children' + (expanded ? '' : ' collapsed') + '" data-depth="' + depth + '" data-loaded="' + (loaded ? '1' : '0') + '" data-parent="' + node.id + '">';
                if (loaded) {
                    html += renderLocationTree(loadedLocChildren[node.id], depth + 1);
                }
                html += '</ul>';
            }
            html += '</li>';
        }
        html += '</ul>';
        return html;
    }

    window._filterByLocation = async function (locId, locName) {
        if (currentLocationFilter === locId) {
            currentLocationFilter = null;
            currentLocationFilterName = '';
            locationFilterIds = null;
        } else {
            currentLocationFilter = locId;
            currentLocationFilterName = locName || '';
            try {
                var resp = await fetchJSON('/api/locations/descendants?id=' + locId);
                locationFilterIds = resp.ids;
            } catch (err) {
                locationFilterIds = [locId];
            }
        }
        document.getElementById('adminLocationTree').innerHTML = renderLocationTree(allData.location_tree);
        renderRestaurants(document.getElementById('adminSearch').value);
    };

    window._editLocation = function (id, name, parentId) {
        var bodyHTML = '<div class="form-group"><label>名称</label><input type="text" id="editLocName" value="' + escapeHTML(name) + '"></div>';
        openModal('编辑位置', bodyHTML, async function () {
            var newName = document.getElementById('editLocName').value.trim();
            if (!newName) throw new Error('名称不能为空');
            await fetchJSON('/api/admin/locations/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            });
            showToast('已更新', 'success');
        });
    };

    window._moveLocation = function (id, name, parentId) {
        var bodyHTML = '<div class="form-group"><label>将「' + escapeHTML(name) + '」移动到</label>' +
            '<div class="tree-select-wrapper">' +
            '<div class="tree-trigger" id="moveLocTrigger">' +
            '<span class="tree-trigger-text" id="moveLocText">顶级位置</span>' +
            '<span class="tree-trigger-arrow">▼</span>' +
            '</div>' +
            '<div class="tree-dropdown" id="moveLocDropdown"></div></div>' +
            '<input type="hidden" id="moveLocParent" value="' + (parentId || '') + '"></div>';
        openModal('移动位置', bodyHTML, async function () {
            var newParent = document.getElementById('moveLocParent').value;
            await fetchJSON('/api/admin/locations/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent_id: newParent ? parseInt(newParent) : null })
            });
            showToast('已移动', 'success');
        });
        setTimeout(function () {
            var trigger = document.getElementById('moveLocTrigger');
            var dropdown = document.getElementById('moveLocDropdown');
            var hidden = document.getElementById('moveLocParent');
            var textEl = document.getElementById('moveLocText');
            if (parentId) {
                fetchJSON('/api/locations/path?id=' + parentId).then(function (info) {
                    textEl.textContent = info.path || ('#' + parentId);
                }).catch(function () { textEl.textContent = '#' + parentId; });
            }
            setupLazyPicker(trigger, dropdown, function (pickId, pickName) {
                if (pickId === id) { showToast('不能将自己设为父级', 'error'); return; }
                hidden.value = pickId;
                textEl.textContent = pickName;
                dropdown.style.display = 'none';
            });
        }, 100);
    };

    window._addChildLocation = function (parentId) {
        var bodyHTML = '<div class="form-group"><label>子位置名称</label><input type="text" id="newChildLocName" placeholder="多层用 / 或 - 分隔，如：xx校区-xx楼"></div>';
        openModal('添加子位置', bodyHTML, async function () {
            var name = document.getElementById('newChildLocName').value.trim();
            if (!name) throw new Error('名称不能为空');
            await fetchJSON('/api/admin/locations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, parent_id: parentId })
            });
            showToast('已添加', 'success');
        });
    };

    window._deleteLocation = async function (id, name, hasKids) {
        var msg = '确定要删除「' + name + '」吗？';
        if (hasKids) msg += '\n\n该位置下有子位置，会一并删除。';
        msg += '\n\n此操作不可撤销！';
        if (!confirm(msg)) return;
        try {
            await fetchJSON('/api/admin/locations/' + id, { method: 'DELETE' });
            showToast('已删除', 'success');
            currentLocationFilter = null;
            await loadAllData();
        } catch (err) { showToast(err.message, 'error'); }
    };

    // ── Restaurant list ─────────────────────────────────────────────────────

    function renderRestaurants(filter) {
        filter = (filter || '').toLowerCase();
        var container = document.getElementById('adminRestaurantList');

        var locFilterIds = locationFilterIds;

        var filtered = allData.restaurants.filter(function (r) {
            if (locFilterIds && locFilterIds.indexOf(r.location_id) === -1) return false;
            if (filter && r.name.toLowerCase().indexOf(filter) === -1 && r.location_name.toLowerCase().indexOf(filter) === -1) return false;
            return true;
        });

        var html = '';
        if (currentLocationFilter) {
            html += '<div style="margin-bottom:12px;color:#e67e22;font-size:13px">当前筛选: ' + escapeHTML(currentLocationFilterName) +
                ' <button class="btn-sm" onclick="window._filterByLocation(' + currentLocationFilter + ')" style="font-size:11px">清除筛选</button></div>';
        }

        for (var i = 0; i < filtered.length; i++) {
            var r = filtered[i];
            html += '<div class="admin-restaurant-card">';
            html += '<div class="admin-restaurant-header" onclick="var b=this.nextElementSibling;if(b){b.classList.toggle(\'collapsed\');}">';
            html += '<div><h4>' + escapeHTML(r.name) + ' <span style="color:#999;font-size:12px;font-weight:normal">[' + escapeHTML(r.location_name) + ']</span></h4>';
            html += '<div class="admin-restaurant-meta">' + (r.taste_avg != null ? '味道 ' + r.taste_avg : '-') + ' | 性价比 ' + (r.value_avg != null ? r.value_avg : '-') + ' | ' + (r.review_count || 0) + '条评价</div></div></div>';
            html += '<div class="admin-restaurant-body collapsed">';
            for (var j = 0; j < (r.reviews || []).length; j++) {
                var rev = r.reviews[j];
                html += '<div class="admin-review-item"><div class="admin-review-info">';
                html += '<div class="admin-review-comment">' + renderMarkdown(rev.comment) + '</div>';
                html += '<div class="meta">' + escapeHTML(rev.username) + ' · ' + rev.date;
                if (rev.taste_score != null) html += ' · 味道' + rev.taste_score;
                if (rev.value_score != null) html += ' · 性价比' + rev.value_score;
                html += '</div></div>';
                html += '<button class="admin-review-delete" onclick="window._deleteReview(' + rev.id + ')">删除</button>';
                html += '</div>';
            }
            html += '<div class="admin-restaurant-actions">';
            html += '<button onclick="window._editRestaurant(' + r.id + ',\'' + escapeHTML(r.name) + '\',' + r.location_id + ',' + r.is_closed + ')">编辑信息</button>';
            html += '<button class="btn-danger" onclick="window._toggleClose(' + r.id + ',' + r.is_closed + ')">' + (r.is_closed ? '重新开业' : '标记关闭') + '</button>';
            html += '<button class="btn-danger" onclick="window._deleteRestaurant(' + r.id + ',\'' + escapeJS(r.name) + '\',' + (r.review_count || 0) + ')">删除饭店</button>';
            html += '</div>';
            html += '<div class="admin-tag-row" style="margin-top:6px;font-size:12px">';
            if (r.tags) for (var k = 0; k < r.tags.length; k++) {
                html += '<span class="tag-badge-sm">' + escapeHTML(r.tags[k]) + ' <span class="tag-remove" onclick="event.stopPropagation();window._removeTag(' + r.id + ',' + k + ',\'' + escapeHTML(r.tags[k]) + '\')">×</span></span>';
            }
            html += '<input type="text" class="admin-tag-input" placeholder="+标签" onkeydown="if(event.key===\'Enter\'){event.preventDefault();event.stopPropagation();window._addAdminTag(' + r.id + ',this.value);this.value=\'\';}">';
            html += '</div>';
            html += '</div></div>';
        }
        container.innerHTML = html || '<div class="empty-state">无匹配饭店</div>';
    }

    window._deleteReview = async function (reviewId) {
        if (!confirm('确定删除这条评价吗？')) return;
        try {
            await fetchJSON('/api/admin/reviews/' + reviewId, { method: 'DELETE' });
            showToast('评价已删除', 'success');
            await loadAllData();
        } catch (err) { showToast(err.message, 'error'); }
    };

    window._deleteRestaurant = async function (id, name, reviewCount) {
        var msg = '确定删除「' + name + '」吗？';
        msg += '\n\n将同时删除该饭店的 ' + reviewCount + ' 条评价和标签关联（标签本身保留，其他饭店不受影响）。';
        msg += '\n\n此操作不可撤销！';
        if (!confirm(msg)) return;
        try {
            await fetchJSON('/api/admin/restaurants/' + id, { method: 'DELETE' });
            showToast('饭店已删除', 'success');
            await loadAllData();
        } catch (err) { showToast(err.message, 'error'); }
    };

    window._editRestaurant = function (id, name, locId, isClosed) {
        var bodyHTML =
            '<div class="form-group"><label>店名</label><input type="text" id="editRestName" value="' + escapeHTML(name) + '"></div>' +
            '<div class="form-group"><label>位置</label>' +
            '<div class="tree-select-wrapper">' +
            '<div class="tree-trigger" id="editRestLocTrigger">' +
            '<span class="tree-trigger-text" id="editRestLocText">选择位置（留空为无位置）</span>' +
            '<span class="tree-trigger-arrow">▼</span>' +
            '</div>' +
            '<div class="tree-dropdown" id="editRestLocDropdown"></div></div>' +
            '<input type="hidden" id="editRestLoc" value="' + (locId || '') + '">' +
            '<button type="button" class="btn-sm" id="editRestLocClear" style="margin-top:6px">清除位置</button></div>';
        openModal('编辑饭店', bodyHTML, async function () {
            var newName = document.getElementById('editRestName').value.trim();
            var newLocId = document.getElementById('editRestLoc').value;
            if (!newName) throw new Error('店名不能为空');
            await fetchJSON('/api/admin/restaurants/' + id, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName, location_id: newLocId ? parseInt(newLocId) : null, is_closed: isClosed })
            });
            showToast('已更新', 'success');
        });
        setTimeout(function () {
            var trigger = document.getElementById('editRestLocTrigger');
            var dropdown = document.getElementById('editRestLocDropdown');
            var hidden = document.getElementById('editRestLoc');
            var textEl = document.getElementById('editRestLocText');
            if (locId) {
                fetchJSON('/api/locations/path?id=' + locId).then(function (info) {
                    textEl.textContent = info.path || ('#' + locId);
                }).catch(function () { textEl.textContent = '#' + locId; });
            }
            setupLazyPicker(trigger, dropdown, function (pickId, pickName) {
                hidden.value = pickId;
                textEl.textContent = pickName;
                dropdown.style.display = 'none';
            });
            document.getElementById('editRestLocClear').addEventListener('click', function () {
                hidden.value = '';
                textEl.textContent = '选择位置（留空为无位置）';
            });
        }, 100);
    };

    window._toggleClose = async function (id, isClosed) {
        await fetchJSON('/api/admin/restaurants/' + id, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_closed: !isClosed })
        });
        showToast(isClosed ? '已重新开业' : '已关闭', 'success');
        await loadAllData();
    };

    window._addAdminTag = async function (rid, name) {
        if (!name.trim()) return;
        await fetchJSON('/api/admin/restaurants/' + rid + '/tags', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) });
        loadAllData();
    };
    window._removeTag = async function (rid, idx, name) {
        var tags = allData.restaurants.find(function (r) { return r.id === rid; }).tags;
        var tagId = tags ? tags[idx] : null;
        if (!tagId) { loadAllData(); return; }
        var allTagsResp = await fetchJSON('/api/tags');
        var tagObj = allTagsResp.find(function (t) { return t.name === name; });
        if (tagObj) {
            await fetchJSON('/api/admin/restaurants/' + rid + '/tags/' + tagObj.id, { method: 'DELETE' });
            loadAllData();
        } else {
            loadAllData();
        }
    };

    // ── Init ─────────────────────────────────────────────────────────────────

    async function ensureExpandedLoaded() {
        var pending = [];
        for (var pid in expandedLocIds) {
            if (expandedLocIds[pid] && !loadedLocChildren[pid]) pending.push(parseInt(pid));
        }
        for (var i = 0; i < pending.length; i++) {
            try {
                await fetchLocationChildren(pending[i]);
                document.getElementById('adminLocationTree').innerHTML = renderLocationTree(allData.location_tree);
            } catch (e) { break; }
        }
    }

    async function loadAllData() {
        try {
            allData = await fetchJSON('/api/admin/all-data');
            loadedLocChildren = {};
            document.getElementById('adminLocationTree').innerHTML = renderLocationTree(allData.location_tree);
            renderRestaurants(document.getElementById('adminSearch').value);
            setupAdminLocationTree();
            ensureExpandedLoaded();
        } catch (err) {
            if (err.message.indexOf('登录') > -1 || err.message.indexOf('401') > -1) {
                window.location.href = '/admin';
            } else {
                showToast('加载失败: ' + err.message, 'error');
            }
        }
    }

    document.getElementById('adminLocationTree').addEventListener('click', async function (e) {
        var tgl = e.target.closest('.tree-toggle[data-tgl-id]');
        if (!tgl) return;
        e.stopPropagation();
        var header = tgl.parentElement;
        var ul = header.nextElementSibling;
        if (!ul || !ul.classList.contains('admin-loc-children')) return;
        var pid = parseInt(tgl.dataset.tglId);
        if (ul.classList.contains('collapsed')) {
            ul.classList.remove('collapsed');
            tgl.classList.add('expanded');
            expandedLocIds[pid] = true;
            if (ul.dataset.loaded !== '1') {
                tgl.textContent = '…';
                try {
                    var kids = await fetchLocationChildren(pid);
                    ul.innerHTML = renderLocationTree(kids, parseInt(ul.dataset.depth) + 1);
                    ul.dataset.loaded = '1';
                } catch (err) {
                    showToast('加载失败', 'error');
                }
                tgl.textContent = '▶';
            }
        } else {
            ul.classList.add('collapsed');
            tgl.classList.remove('expanded');
            expandedLocIds[pid] = false;
        }
    });

    document.getElementById('adminSearch').addEventListener('input', function () {
        renderRestaurants(this.value);
    });

    document.getElementById('addLocBtn').addEventListener('click', async function () {
        var name = document.getElementById('newLocName').value.trim();
        if (!name) { showToast('请输入名称', 'error'); return; }
        var parentId = document.getElementById('newLocParent').value;
        try {
            await fetchJSON('/api/admin/locations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, parent_id: parentId ? parseInt(parentId) : null })
            });
            document.getElementById('newLocName').value = '';
            showToast('位置已添加', 'success');
            await loadAllData();
        } catch (err) { showToast(err.message, 'error'); }
    });

    loadAllData();

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) loadAllData();
    });

    // ── Panel resize ────────────────────────────────────────────────────────
    (function () {
        var handle = document.getElementById('adminResizeHandle');
        var left = document.getElementById('adminLeft');
        if (!handle || !left) return;

        var dragging = false;
        var startX, startW;

        function onDown(e) {
            e.preventDefault();
            dragging = true;
            startX = e.touches ? e.touches[0].clientX : e.clientX;
            startW = left.offsetWidth;
            handle.classList.add('active');
            document.body.style.cursor = 'ew-resize';
            document.body.style.userSelect = 'none';
        }

        function onMove(e) {
            if (!dragging) return;
            var x = e.touches ? e.touches[0].clientX : e.clientX;
            var w = startW + (x - startX);
            if (w < 200) w = 200;
            if (w > 800) w = 800;
            left.style.width = w + 'px';
        }

        function onUp() {
            if (!dragging) return;
            dragging = false;
            handle.classList.remove('active');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }

        handle.addEventListener('mousedown', onDown);
        handle.addEventListener('touchstart', onDown, { passive: false });
        document.addEventListener('mousemove', onMove);
        document.addEventListener('touchmove', onMove, { passive: false });
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchend', onUp);
    })();
})();
