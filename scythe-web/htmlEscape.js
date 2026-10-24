/** Text for use inside HTML: the five characters that can end a text node or an attribute value. Numbers and other values are stringified. */
export const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
