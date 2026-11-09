// dm-notes.js — The DM notes tab: what the game master wrote to this player
// (`api user notes` in the console). Private to this account; read-only.

export const schemas = ["notes/1"];

export function mount(element) {
  const root = document.createElement("div");
  element.append(root);
  return {
    update(state) {
      root.className = state.text ? "notes" : "notes empty";
      root.textContent = state.text || "No notes from the game master yet.";
    },
    unmount() {
      root.remove();
    },
  };
}
