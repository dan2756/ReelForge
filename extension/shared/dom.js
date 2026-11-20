// The popup and the options page build what they show node by node, never from a string of HTML: everything
// they show comes from ReelForge or from a platform's page.
export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag)
  for (const [name, value] of Object.entries(props)) {
    if (value == null || value === false) continue
    if (name === "class") el.className = value
    else if (name === "text") el.textContent = value
    else if (name === "data") Object.assign(el.dataset, value)
    else if (name.startsWith("on")) el.addEventListener(name.slice(2), value)
    else el.setAttribute(name, value === true ? "" : value)
  }
  for (const kid of kids.flat(3)) if (kid != null && kid !== false) el.append(kid)
  return el
}
