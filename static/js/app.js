const App = {
    state: {
        papers: [],
        activePaper: null,
        script: null,
        chunks: [],
        currentChunk: 0,
        playing: false,
    },

    mixer: new AudioMixer(),

    async init() {
        this.bindEvents();
        await this.loadPapers();
    },

    bindEvents() {
        // PDF upload
        UI.$('pdf-input').onchange = (e) => {
            if (e.target.files[0]) this.uploadPaper(e.target.files[0]);
        };
        const dropZone = UI.$('pdf-drop');
        dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); };
        dropZone.ondragleave = () => dropZone.classList.remove('drag-over');
        dropZone.ondrop = (e) => {
            e.preventDefault();
            dropZone.classList.remove('drag-over');
            const file = e.dataTransfer.files[0];
            if (file && file.name.endsWith('.pdf')) this.uploadPaper(file);
        };

        // Pipeline
        UI.$('btn-generate').onclick = () => this.runPipeline();
        UI.$('btn-render').onclick = () => this.runRender();

        // Speed display
        UI.$('tts-speed').oninput = (e) => {
            UI.$('tts-speed-val').textContent = `${e.target.value}x`;
        };

        // Player controls
        UI.$('btn-play').onclick = () => this.togglePlay();
        UI.$('btn-prev').onclick = () => this.prevChunk();
        UI.$('btn-next').onclick = () => this.nextChunk();

        // Volume
        UI.$('vol-speech').oninput = (e) => this.mixer.setSpeechVolume(parseFloat(e.target.value));

        // Export
        UI.$('btn-export').onclick = () => this.exportVoiceover();
        UI.$('btn-export-video').onclick = () => this.exportVideo();

        // Speech ended -> next chunk
        this.mixer.onSpeechEnded(() => this.nextChunk());
    },

    // --- Papers ---
    async loadPapers() {
        this.state.papers = await API.listPapers();
        UI.renderPaperList(this.state.papers, this.state.activePaper?.id, (p) => this.selectPaper(p));
    },

    async uploadPaper(file) {
        try {
            const meta = await API.uploadPaper(file);
            this.state.papers.push(meta);
            UI.renderPaperList(this.state.papers, null, (p) => this.selectPaper(p));
            this.selectPaper(meta);
        } catch (e) {
            alert('Upload failed: ' + e.message);
        }
    },

    async selectPaper(paper) {
        this.state.activePaper = paper;
        this.state.script = null;
        this.state.chunks = [];
        this.state.currentChunk = 0;
        this.state.playing = false;

        UI.renderPaperList(this.state.papers, paper.id, (p) => this.selectPaper(p));
        UI.$('text-title').textContent = paper.filename;
        UI.$('pipeline-controls').style.display = 'flex';
        UI.$('pipeline-stages').style.display = 'none';

        // Show raw sections initially
        UI.renderSections(paper.sections, -1);

        // Check for existing script
        const script = await API.getScript(paper.id);
        if (script) {
            this.state.script = script;
            UI.renderScript(script, -1);
            UI.renderPipelineStages('done', 'completed');
            UI.$('btn-render').style.display = '';
        } else {
            UI.$('btn-render').style.display = 'none';
        }

        // Check for existing audio
        const chunks = await API.listPipelineAudio(paper.id);
        if (chunks.length > 0) {
            this.state.chunks = chunks;
            this.state.currentChunk = 0;
            UI.setPlayerVisible(true);
            UI.updateChunkIndicator(1, chunks.length);
        } else {
            UI.setPlayerVisible(false);
        }

        // Check for existing video
        if (script && script.video_file) {
            UI.showVideoPlayer(API.videoURL(paper.id));
        } else {
            UI.hideVideoPlayer();
        }
    },

    // --- Pipeline ---
    async runPipeline() {
        const paper = this.state.activePaper;
        if (!paper) return;

        const voice = UI.$('voice-select').value;
        const speed = parseFloat(UI.$('tts-speed').value);

        UI.$('btn-generate').disabled = true;

        await API.startPipeline(paper.id, voice, speed);

        API.streamPipeline(paper.id, async (data) => {
            UI.showPipelineProgress(data);

            // When scripting completes, load script preview
            if (data.stage === 'voiceover' && !this.state.script) {
                const script = await API.getScript(paper.id);
                if (script) {
                    this.state.script = script;
                    UI.renderScript(script, -1);
                }
            }

            // Progressive audio: show player as voiceover chunks arrive
            if (data.stage === 'voiceover' && data.current_chunk >= 1) {
                this.refreshChunks();
            }

            if (data.status === 'completed') {
                UI.$('btn-generate').disabled = false;
                UI.$('btn-render').style.display = '';
                // Reload final script with actual durations
                const script = await API.getScript(paper.id);
                if (script) {
                    this.state.script = script;
                    UI.renderScript(script, -1);
                    // Show video player if video was generated
                    if (script.video_file) {
                        UI.showVideoPlayer(API.videoURL(paper.id));
                    }
                }
                this.refreshChunks();
            } else if (data.status === 'failed') {
                UI.$('btn-generate').disabled = false;
                alert('Pipeline failed: ' + data.message);
            }
        });
    },

    async runRender() {
        const paper = this.state.activePaper;
        if (!paper) return;

        UI.$('btn-render').disabled = true;
        UI.$('btn-generate').disabled = true;

        await API.startRender(paper.id);

        API.streamRender(paper.id, async (data) => {
            UI.showPipelineProgress(data);

            if (data.status === 'completed') {
                UI.$('btn-render').disabled = false;
                UI.$('btn-generate').disabled = false;
                const script = await API.getScript(paper.id);
                if (script) {
                    this.state.script = script;
                    UI.renderScript(script, -1);
                    if (script.video_file) {
                        UI.showVideoPlayer(API.videoURL(paper.id));
                    }
                }
            } else if (data.status === 'failed') {
                UI.$('btn-render').disabled = false;
                UI.$('btn-generate').disabled = false;
                alert('Render failed: ' + data.message);
            }
        });
    },

    async refreshChunks() {
        const paper = this.state.activePaper;
        if (!paper) return;
        const chunks = await API.listPipelineAudio(paper.id);
        this.state.chunks = chunks;
        if (chunks.length > 0) {
            UI.setPlayerVisible(true);
            UI.updateChunkIndicator(this.state.currentChunk + 1, chunks.length);
        }
    },

    // --- Playback ---
    async togglePlay() {
        if (this.state.chunks.length === 0) return;

        if (this.mixer.isPlaying) {
            this.mixer.pauseSpeech();
            this.state.playing = false;
        } else {
            if (this.mixer.speechEl.src && !this.mixer.speechEl.ended) {
                await this.mixer.resumeSpeech();
            } else {
                await this.playChunk(this.state.currentChunk);
            }
            this.state.playing = true;
        }
        UI.updatePlayButton(this.state.playing);
    },

    async playChunk(index) {
        if (index < 0 || index >= this.state.chunks.length) {
            this.state.playing = false;
            UI.updatePlayButton(false);
            return;
        }

        this.state.currentChunk = index;
        const chunk = this.state.chunks[index];
        const url = API.pipelineAudioURL(this.state.activePaper.id, chunk.filename);
        await this.mixer.playSpeech(url);

        UI.updateChunkIndicator(index + 1, this.state.chunks.length);
        UI.highlightChunk(index);
        UI.scrollToChunk(index);
    },

    nextChunk() {
        if (this.state.playing || this.mixer.isPlaying) {
            this.playChunk(this.state.currentChunk + 1);
        } else {
            this.state.currentChunk = Math.min(this.state.currentChunk + 1, this.state.chunks.length - 1);
            UI.updateChunkIndicator(this.state.currentChunk + 1, this.state.chunks.length);
            UI.highlightChunk(this.state.currentChunk);
            UI.scrollToChunk(this.state.currentChunk);
        }
    },

    prevChunk() {
        const newIdx = Math.max(this.state.currentChunk - 1, 0);
        if (this.state.playing || this.mixer.isPlaying) {
            this.playChunk(newIdx);
        } else {
            this.state.currentChunk = newIdx;
            UI.updateChunkIndicator(this.state.currentChunk + 1, this.state.chunks.length);
            UI.highlightChunk(this.state.currentChunk);
            UI.scrollToChunk(this.state.currentChunk);
        }
    },

    // --- Export ---
    async exportVoiceover() {
        const paper = this.state.activePaper;
        if (!paper) return;

        UI.$('btn-export').disabled = true;
        UI.$('btn-export').textContent = 'Exporting...';

        try {
            const blob = await API.exportVoiceover(paper.id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${paper.filename.replace('.pdf', '')}_voiceover.mp3`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            alert('Export failed: ' + e.message);
        }

        UI.$('btn-export').disabled = false;
        UI.$('btn-export').textContent = 'Download Voiceover';
    },
    // --- Video Export ---
    async exportVideo() {
        const paper = this.state.activePaper;
        if (!paper) return;

        UI.$('btn-export-video').disabled = true;
        UI.$('btn-export-video').textContent = 'Exporting...';

        try {
            const blob = await API.exportVideo(paper.id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${paper.filename.replace('.pdf', '')}_video.mp4`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            alert('Video export failed: ' + e.message);
        }

        UI.$('btn-export-video').disabled = false;
        UI.$('btn-export-video').textContent = 'Download Video';
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
