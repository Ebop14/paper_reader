# TODO

## V1 — Stabilization & Polish

- [ ] End-to-end test: upload PDF → generate video → verify script.json + WAV files + playback
- [ ] Handle re-runs gracefully (clear old audio/script when pipeline is re-triggered for same paper)
- [ ] Add error display in UI when pipeline fails (currently just an alert)
- [ ] Segment card click-to-play: clicking a segment card should jump to that segment's audio
- [ ] Show estimated total duration in UI before/during voiceover generation
- [ ] Add loading spinner/skeleton while script is being fetched after scripting phase
- [ ] Delete old `routers/music.py` and `routers/mix.py` files (unmounted but still on disk)
- [ ] Add proper logging (replace print/silent failures with structured logging)

## V2 — Animation (Manim)

- [ ] `animation_service.py` — Manim renderer that reads `animation_hints` from script segments
- [ ] Implement animation types: equation (LaTeX write), bullet_list (fade_in items), diagram (placeholder), highlight, graph
- [ ] `compositor_service.py` — stitch rendered animation clips with voiceover audio into final video
- [ ] Add `ffmpeg` video concat + audio overlay
- [ ] Pipeline Phase 4: animation rendering (between voiceover and done)
- [ ] Pipeline Phase 5: compositing (final video assembly)
- [ ] Frontend video player (replace audio-only player)
- [ ] Script editor: allow editing narration_text and animation_hints before rendering

## V2.5 — Quality & UX

- [ ] Director agent: Claude call to plan section ordering, emphasis, and pacing before scriptwriting
- [ ] Script review agent: Claude call after aggregation to check for quality, coherence, accuracy
- [ ] Multiple voice support per script (e.g. narrator + "expert" voice for quotes)
- [ ] Background music layer (re-enable music upload/generation for video soundtrack)
- [ ] Waveform visualization in player
- [ ] Keyboard shortcuts (space = play/pause, arrow keys = prev/next)

## Infrastructure

- [ ] Persistent task storage (SQLite or Redis) so progress survives server restart
- [ ] Authentication / API keys
- [ ] Rate limiting on pipeline starts
- [ ] Data cleanup: auto-delete old papers/audio/scripts after N days or total size limit
- [ ] CI: syntax check + import check on all Python files
- [ ] Dockerfile for reproducible deployment
