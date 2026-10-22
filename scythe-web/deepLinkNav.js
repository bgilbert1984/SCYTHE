import {PAGES, deepLink} from "./deepLink.js";

/**
 * A row of links to the other pages, carrying the page's mission and its current selection. Call update() when the
 * selection changes. Plain anchors: they work without script, open in a new tab on middle-click, and can be copied.
 */
export function mountDeepLinkNav({root, from, targets, mission = null, select = null, rejected = [], document: doc = globalThis.document}) {
  const nav = doc.createElement("nav");
  nav.className = "deeplink-nav";
  nav.setAttribute("aria-label", "Open this mission in another view");
  if (rejected.length) {   // said, not silently dropped: the page is not showing what the link asked for
    const note = doc.createElement("span");
    note.className = "deeplink-nav__note";
    note.textContent = `ignored in link: ${rejected.join(", ")}`;
    nav.append(note);
  }
  const anchors = targets.map((target) => {
    const anchor = doc.createElement("a");
    anchor.textContent = PAGES[target].label;
    anchor.dataset.target = target;
    nav.append(anchor);
    return {target, anchor};
  });
  const state = {mission, select};
  const update = (next = {}) => {
    Object.assign(state, next);
    for (const {target, anchor} of anchors) {
      const href = deepLink(target, state, from);
      anchor.href = href;
      anchor.title = state.select ? `Open ${PAGES[target].label} on ${state.select}` : `Open ${PAGES[target].label}`;
    }
  };
  update();
  root.append(nav);
  return {nav, update};
}
