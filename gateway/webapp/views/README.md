# The webapp's own views

A file here, named after a feature (`combat.js`), replaces that feature's
default view in the webapp: with `[api.views] <feature> = "auto"` the
webapp's view comes first, then the feature's, then the generic one.

A view is a JS module:

```js
export const schemas = ["combat/1"];      // the state versions it understands

export function mount(element, ctx) {
  // ctx: user, role, character, actions (from the app), send(action, args)
  return {
    update(state) { /* draw the feature's public state */ },
    unmount() { /* remove what mount added */ },
  };
}
```

For a state version not in `schemas`, the tab falls back to the feature's
view. Build the page with `textContent`, never `innerHTML`: names come
from the table. The webapp's content security policy allows no inline
styles or scripts; a view brings its styles as a constructed stylesheet
(`document.adoptedStyleSheets`) or uses the classes in `static/style.css`.
