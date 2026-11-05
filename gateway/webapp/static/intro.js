// intro.js — A character's intro while the webapp loads (see gateway/intros.py).
//
// The loop clips play, silently and in random order, until the webapp is
// loaded: at least `minimumMs`, the table connected and every tab showing its
// state (`isReady()`). Then the button: its tap turns the sound on, the loop
// clip that is playing finishes, and the finale plays. It ends on white,
// which fades into the webapp. "Skip" (or Esc) goes straight there.
//
// Two video elements take turns: the next clip waits underneath on its first
// frame and comes to the front once it shows a frame, so the clips join
// without a gap.

export const MINIMUM_MS = 10000;
const SWAP_MARGIN = 0.8;        // seconds: later than this, the finale waits one more clip

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function clip() {
  const video = el("video", "intro-clip");
  video.muted = true;
  video.playsInline = true;
  video.setAttribute("playsinline", "");
  video.preload = "auto";
  return video;
}

export function playIntro(intro, {isReady, onDone, minimumMs = MINIMUM_MS}) {
  const root = el("div", "intro");
  if (intro.color) root.style.setProperty("--intro-color", intro.color);
  const vids = [clip(), clip()];
  const ui = el("div", "intro-ui");
  const label = el("p", "intro-label", intro.loading || "Loading…");
  const bar = el("div", "intro-bar");
  const fill = el("div", "intro-fill");
  bar.append(fill);
  const buttons = el("div", "intro-buttons");
  buttons.hidden = true;
  const enter = el("button", "intro-enter", intro.button || "Enter");
  enter.type = "button";
  const skip = el("button", "intro-skip", "Skip ›");
  skip.type = "button";
  buttons.append(enter, skip);
  const hint = el("p", "intro-hint", "Sound on, so mind your volume.");
  hint.hidden = true;
  ui.append(label, bar, buttons, hint);
  const turn = el("p", "intro-turn", "Turn your phone sideways for the full view.");
  root.append(...vids, ui, turn);
  const flash = el("div", "intro-flash");
  document.body.append(root, flash);
  document.body.classList.add("intro-running");

  const started = performance.now();
  let state = "loading";      // → ready → finishing → finale → done
  let cur = vids[0], nxt = vids[1], finish = false, frame = 0;

  const pick = () => intro.loop[Math.floor(Math.random() * intro.loop.length)];

  function prepare(video, url) {        // park on the clip's first frame
    if (video.getAttribute("src") !== url) {
      video.src = url;
    } else {
      video.pause();
      video.currentTime = 0;
    }
  }

  function toFront(video, then) {       // once it has shown a frame
    const go = () => {
      for (const v of vids) v.classList.toggle("front", v === video);
      if (then) then();
    };
    if ("requestVideoFrameCallback" in video) video.requestVideoFrameCallback(go);
    else video.addEventListener("playing", go, {once: true});
  }

  function play(video) {
    const result = video.play();
    if (result && result.catch) {
      result.catch(() => {              // sound refused: play it silently
        video.muted = true;
        video.play().catch(() => {});
      });
    }
  }

  function advance() {
    const old = cur;
    cur = nxt;
    play(cur);
    if (cur.getAttribute("src") === intro.finale) {
      state = "finale";
      ui.classList.add("gone");
      nxt = null;
      toFront(cur, () => old.pause());
      cur.addEventListener("ended", reveal, {once: true});
      return;
    }
    nxt = old;
    toFront(cur, () => {
      old.pause();
      prepare(old, finish ? intro.finale : pick());
    });
  }

  for (const video of vids) {
    video.addEventListener("ended", () => {
      if (video === cur && nxt && state !== "done") advance();
    });
  }

  enter.addEventListener("click", () => {
    if (state !== "ready") return;
    state = "finishing";
    finish = true;
    buttons.hidden = true;
    hint.hidden = true;
    label.textContent = intro.after_click || "";
    // The tap allows sound: unmute both, and start the waiting one once
    // under the tap so phones let it play with sound later.
    for (const v of vids) v.muted = false;
    const left = (cur.duration || 0) - (cur.currentTime || 0);
    if (left > SWAP_MARGIN) prepare(nxt, intro.finale);   // else it follows one clip later
    const waiting = nxt;
    const primed = waiting.play();
    if (primed && primed.then) {
      primed.then(() => {
        if (waiting !== cur) {
          waiting.pause();
          waiting.currentTime = 0;
        }
      }).catch(() => {});
    }
  });

  skip.addEventListener("click", () => reveal());
  function onKey(event) {
    if (event.key === "Escape" && state !== "loading") reveal();
  }
  document.addEventListener("keydown", onKey);

  function reveal() {
    if (state === "done") return;
    state = "done";
    cancelAnimationFrame(frame);
    document.removeEventListener("keydown", onKey);
    flash.classList.add("on");          // white, like the finale's last frame
    for (const v of vids) {             // let go of the clips
      v.pause();
      v.removeAttribute("src");
      v.load();
    }
    root.remove();
    document.body.classList.remove("intro-running");
    if (onDone) onDone();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      flash.classList.remove("on");     // fades the white away
      setTimeout(() => flash.remove(), 1200);
    }));
  }

  function tick() {
    if (state === "loading") {
      const elapsed = performance.now() - started;
      fill.style.width = `${Math.min(100, (elapsed / minimumMs) * 100)}%`;
      if (elapsed >= minimumMs) {
        if (isReady()) {
          state = "ready";
          label.textContent = "Ready";
          bar.hidden = true;
          buttons.hidden = false;
          hint.hidden = false;
          enter.focus();
        } else {
          label.textContent = "Waiting for the table…";
          bar.classList.add("waiting");
        }
      }
    }
    if (state !== "done") frame = requestAnimationFrame(tick);
  }

  prepare(vids[0], pick());
  prepare(vids[1], pick());
  vids[0].classList.add("front");
  play(vids[0]);
  frame = requestAnimationFrame(tick);

  return {stop: reveal, get state() { return state; }};
}
