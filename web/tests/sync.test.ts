// SyncGroup keeps every pane on the same film frame, including partial cuts whose own time 0 is later in the film.
// Fake <video> elements stand in for the browser; `npm test` runs this with Node's built-in TypeScript support.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { SyncGroup } from "../src/sync.ts";
import { cutCovers, FPS, mediaUrl } from "../src/util.ts";

class FakeVideo {
  currentTime = 0;
  readyState = 4;
  paused = true;
  ended = false;
  muted = false;
  playbackRate = 1;
  duration: number;
  private on: Record<string, (() => void)[]> = {};
  constructor(duration: number) {
    this.duration = duration;
  }
  addEventListener(type: string, fn: () => void) {
    (this.on[type] ??= []).push(fn);
  }
  play() {
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  /** The browser presented the frame at the current time. */
  present() {
    this.on.timeupdate?.forEach((f) => f());
  }
}

const g = globalThis as unknown as Record<string, unknown>;
g.requestAnimationFrame = () => 1;
g.cancelAnimationFrame = () => undefined;

// the real partial test cut: s05-s06 = film frames 465..697 (233 frames), next to a full 4882-frame film
const S05 = 465;
const S06_END = 698;
let group: SyncGroup;
let full: FakeVideo;
let part: FakeVideo;
const near = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);
const tick = () => (group as unknown as { tick: () => void }).tick();

beforeEach(() => {
  group = new SyncGroup();
  group.filmLength = 4882 / FPS;
  full = new FakeVideo(4882 / FPS);
  part = new FakeVideo((S06_END - S05) / FPS);
  group.register(0, full as unknown as HTMLVideoElement);
  group.register(1, part as unknown as HTMLVideoElement);
  group.setRange(0, 0, null);
  group.setRange(1, S05, S06_END);
});

test("a film frame maps to each pane's own frame", () => {
  group.seekFrame(500);
  assert.equal(group.state.frame, 500);
  near(full.currentTime, 500.5 / FPS);
  near(part.currentTime, (500 - S05 + 0.5) / FPS);           // cut-local frame 35, not film frame 500
});

test("outside a partial cut its pane is parked, never seeked to the wrong moment", () => {
  part.play();
  group.seekFrame(100);                                       // before s05
  assert.ok(part.paused);
  near(part.currentTime, 0.5 / FPS);                          // waiting on its first frame
  near(full.currentTime, 100.5 / FPS);
  part.currentTime = 3;
  group.seekFrame(2000);                                      // after s06: left where it is
  assert.ok(part.paused);
  assert.equal(part.currentTime, 3);
  assert.equal(cutCovers({ film_f0: S05, film_f1: S06_END }, 2000), false);
  assert.equal(cutCovers({ film_f0: S05, film_f1: S06_END }, S05), true);
  assert.equal(cutCovers({ film_f0: S05, film_f1: S06_END }, S06_END), false);
  assert.equal(cutCovers({ film_f0: 0, film_f1: null }, 4881), true);
});

test("a partial cut driving the clock reports film time", () => {
  group.setMaster(1);
  group.seekFrame(520);
  part.currentTime = (530 - S05 + 0.2) / FPS;                 // it plays on to its own frame 65
  part.present();
  assert.equal(group.state.frame, 530);
  full.currentTime = 9;                                       // a follower never moves the clock
  full.present();
  assert.equal(group.state.frame, 530);
  group.pause();
  assert.equal(group.state.frame, 530);
  near(full.currentTime, 530.5 / FPS);
  near(part.currentTime, (530 - S05 + 0.5) / FPS);
});

test("outside the master's cut another pane drives the clock and carries the sound", () => {
  group.setMaster(1);
  group.seekFrame(100);
  assert.equal(full.muted, false);
  assert.equal(part.muted, true);
  full.currentTime = 120.5 / FPS;
  full.present();
  assert.equal(group.state.frame, 120);
  group.seekFrame(600);
  assert.equal(part.muted, false);                            // back inside: the master's own sound
  assert.equal(full.muted, true);
});

test("playing into, through and out of a partial cut", () => {
  group.seekFrame(460);
  group.play();
  assert.ok(!full.paused && part.paused);                     // not there yet
  full.currentTime = 466.2 / FPS;
  full.present();
  tick();
  assert.ok(!part.paused);                                    // entered: starts from its first frame
  part.currentTime = 2;                                       // drifted far: pulled back into line
  full.currentTime = 470.2 / FPS;
  group.setMaster(0);
  tick();
  near(part.currentTime, (470.2 - S05) / FPS + 0.05);
  full.currentTime = 700.2 / FPS;
  full.present();
  tick();
  assert.ok(part.paused && !full.paused);                     // left it behind
});

test("when a partial master runs out, a full cut takes the playhead on", () => {
  group.setMaster(1);
  group.seekFrame(690);
  group.play();
  full.currentTime = 697.9 / FPS;
  full.present();                                             // a follower: ignored while the master plays
  assert.equal(group.state.frame, 690);
  part.currentTime = part.duration;
  part.ended = true;
  part.paused = true;
  tick();
  assert.equal(group.state.playing, true);
  assert.equal(full.muted, false);                            // the sound moves with the clock
  full.currentTime = 699.4 / FPS;
  full.present();
  assert.equal(group.state.frame, 699);
});

test("one partial cut hands the playhead to the next", () => {
  const next = new FakeVideo((814 - S06_END) / FPS);          // s07 alone, film frames 698..813
  group.register(0, null);
  group.register(2, next as unknown as HTMLVideoElement);
  group.setRange(2, S06_END, 814);
  group.setMaster(1);
  group.seekFrame(696);
  group.play();
  assert.ok(next.paused);
  near(next.currentTime, 0.5 / FPS);                          // parked on its first frame, ready
  part.currentTime = part.duration;
  part.ended = true;
  part.paused = true;
  tick();
  assert.equal(group.state.frame, S06_END);
  assert.equal(group.state.playing, true);
  assert.ok(!next.paused && !next.muted);
});

test("play outside every open cut starts at the next cut ahead", () => {
  group.register(0, null);
  group.seekFrame(100);
  group.play();
  assert.equal(group.state.frame, S05);
  near(part.currentTime, 0.5 / FPS);
  assert.ok(!part.paused);
});

test("seeks reach the whole film even when only a partial cut is open", () => {
  group.register(0, null);
  group.seekFrame(3000);
  assert.equal(group.state.frame, 3000);
  assert.ok(part.paused);
});

test("media URLs escape each path segment", () => {
  assert.equal(mediaUrl("renders/studio/cuts/a #1&b.mp4"), "/media/renders/studio/cuts/a%20%231%26b.mp4");
  assert.equal(mediaUrl("notes/studio/frames/a-20261005-020302-3aee.png"), "/media/notes/studio/frames/a-20261005-020302-3aee.png");
});
