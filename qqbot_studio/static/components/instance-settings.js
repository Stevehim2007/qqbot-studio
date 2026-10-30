/* QQ Bot Studio — instance settings modal component.
 * Owns modal visibility, scroll lock callback, busy guard, focus return and
 * content refresh. The page supplies section renderers so plugin/memory/resource
 * behavior and existing DOM IDs stay unchanged.
 */
(function (global) {
  'use strict';

  function createInstanceSettings(options = {}) {
    const doc = options.document || global.document;
    const overlayId = options.overlayId || 'instance-settings-overlay';
    const modalId = options.modalId || 'instance-settings-modal';
    const bodyId = options.bodyId || 'instance-settings-body';
    const titleId = options.titleId || 'instance-settings-title';
    const subtitleId = options.subtitleId || 'instance-settings-subtitle';
    const closeId = options.closeId || 'instance-settings-close';
    let open = false;
    let instanceId = '';
    let trigger = null;
    let busy = 0;
    let mounted = false;

    const get = (id) => doc.getElementById(id);
    function setText(id, text) {
      const element = get(id);
      if (element) element.textContent = text;
    }
    function notifyVisibilityChange() {
      if (typeof options.onVisibilityChange === 'function') options.onVisibilityChange(open);
    }
    function syncBusyState() {
      const closeButton = get(closeId);
      const modal = get(modalId);
      if (closeButton) closeButton.disabled = busy > 0;
      if (modal) modal.setAttribute('aria-busy', busy > 0 ? 'true' : 'false');
    }
    function subtitleFor(instance) {
      return typeof options.subtitleText === 'function'
        ? options.subtitleText(instance)
        : `实例 ${instance.name || instance.id} · ID ${instance.id || '缺失'}`;
    }
    function setExpanded(element, expanded) {
      if (element) element.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
    function buildContent(instance) {
      const body = get(bodyId);
      if (!body || typeof options.renderSections !== 'function') return;
      const previousScroll = body.scrollTop || 0;
      body.replaceChildren();
      const sections = options.renderSections(instance);
      for (const section of Array.isArray(sections) ? sections : []) {
        if (section) body.appendChild(section);
      }
      body.scrollTop = previousScroll;
    }
    function findCurrentTrigger() {
      if (!instanceId || typeof options.findTrigger !== 'function') return null;
      return options.findTrigger(instanceId);
    }

    function openModal(instance, triggerElement) {
      if (!instance || !instance.id) return false;
      open = true;
      instanceId = instance.id;
      trigger = triggerElement || null;
      busy = 0;
      const overlay = get(overlayId);
      if (overlay) overlay.hidden = false;
      setText(titleId, `${instance.name || instance.id} · 设置`);
      setText(subtitleId, subtitleFor(instance));
      buildContent(instance);
      setExpanded(trigger, true);
      syncBusyState();
      const modal = get(modalId);
      if (modal) modal.focus({ preventScroll: true });
      notifyVisibilityChange();
      return true;
    }

    function closeModal(closeOptions = {}) {
      if (busy > 0 && closeOptions.force !== true) return false;
      const previousId = instanceId;
      const previousTrigger = trigger;
      open = false;
      instanceId = '';
      trigger = null;
      busy = 0;
      syncBusyState();
      const overlay = get(overlayId);
      if (overlay) overlay.hidden = true;
      const body = get(bodyId);
      if (body) body.replaceChildren();
      if (previousTrigger && previousTrigger.isConnected) {
        setExpanded(previousTrigger, false);
        previousTrigger.focus({ preventScroll: true });
      } else if (previousId) {
        const fallback = findCurrentTrigger() || (typeof options.findTrigger === 'function' ? options.findTrigger(previousId) : null);
        if (fallback) {
          setExpanded(fallback, false);
          fallback.focus({ preventScroll: true });
        }
      }
      notifyVisibilityChange();
      return true;
    }

    function refreshModal() {
      if (!open) return false;
      const instance = typeof options.getInstance === 'function' ? options.getInstance(instanceId) : null;
      if (!instance) {
        closeModal({ force: true });
        return false;
      }
      if (busy > 0 || (typeof options.isBlocked === 'function' && options.isBlocked())) return true;
      setText(titleId, `${instance.name || instance.id} · 设置`);
      setText(subtitleId, subtitleFor(instance));
      const currentTrigger = findCurrentTrigger();
      if (currentTrigger) {
        setExpanded(currentTrigger, true);
        if (!trigger || !trigger.isConnected) trigger = currentTrigger;
      }
      buildContent(instance);
      return true;
    }

    function updateSubtitle(instance) {
      if (!open || !instance || instance.id !== instanceId) return;
      setText(subtitleId, subtitleFor(instance));
    }

    function beginBusy() {
      if (!open) return false;
      busy += 1;
      syncBusyState();
      return true;
    }

    function endBusy() {
      if (busy <= 0) return;
      busy = Math.max(0, busy - 1);
      syncBusyState();
    }

    function mount() {
      if (mounted) return;
      mounted = true;
      const overlay = get(overlayId);
      if (overlay) {
        overlay.addEventListener('click', (event) => {
          if (event.target === overlay) closeModal();
        });
      }
      const closeButton = get(closeId);
      if (closeButton) closeButton.addEventListener('click', () => closeModal());
    }

    return Object.freeze({
      mount,
      open: openModal,
      close: closeModal,
      refresh: refreshModal,
      updateSubtitle,
      beginBusy,
      endBusy,
      isOpen: () => open,
      isOpenFor: (id) => open && instanceId === id,
      isBusy: () => busy > 0,
      currentInstanceId: () => instanceId
    });
  }

  const namespace = global.QQBotStudio = global.QQBotStudio || {};
  namespace.components = namespace.components || {};
  namespace.components.instanceSettings = Object.freeze({ createInstanceSettings });
})(typeof window !== 'undefined' ? window : globalThis);
