const App = {
    state: {
        papers: [],
        activePaper: null,
        displayedSections: null,
        processedMode: null,
        chunks: [],
        currentChunk: 0,
        playing: false,
        musicList: [],
        activeMusic: null,
    },

    mixer: new AudioMixer(),

    async init() {
        this.bindEvents();
        await this.loadPapers();
        await this.loadMusic();
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

        // Processing
        UI.$('btn-process').onclick = () => this.processActivePaper();

        // TTS
        UI.$('tts-speed').oninput = (e) => {
            UI.$('tts-speed-val').textContent = `${e.target.value}x`;
        };
        UI.$('btn-generate-tts').onclick = () => this.generateTTS();

        // Player controls
        UI.$('btn-play').onclick = () => this.togglePlay();
        UI.$('btn-prev').onclick = () => this.prevChunk();
        UI.$('btn-next').onclick = () => this.nextChunk();

        // Volume
        UI.$('vol-speech').oninput = (e) => this.mixer.setSpeechVolume(parseFloat(e.target.value));
        UI.$('vol-music').oninput = (e) => this.mixer.setMusicVolume(parseFloat(e.target.value));

        // Music upload
        UI.$('music-input').onchange = (e) => {
            if (e.target.files[0]) this.uploadMusic(e.target.files[0]);
        };

        // Export
        UI.$('btn-export').onclick = () => this.exportMix();

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
        this.state.displayedSections = paper.sections;
        this.state.processedMode = null;

        UI.renderPaperList(this.state.papers, paper.id, (p) => this.selectPaper(p));
        UI.$('text-title').textContent = paper.filename;
        UI.$('process-controls').style.display = 'flex';
        UI.$('tts-controls').style.display = 'block';
        UI.renderSections(paper.sections, -1);

        // Check for existing processed text
        for (const mode of ['narrated', 'verbatim']) {
            const processed = await API.getProcessed(paper.id, mode);
            if (processed) {
                this.state.displayedSections = processed;
                this.state.processedMode = mode;
                UI.renderSections(processed, -1);
                break;
            }
        }

        // Check for existing audio chunks
        const chunks = await API.listChunks(paper.id);
        if (chunks.length > 0) {
            this.state.chunks = chunks;
            this.state.currentChunk = 0;
            UI.setPlayerVisible(true);
            UI.updateChunkIndicator(1, chunks.length);
        } else {
            this.state.chunks = [];
            UI.setPlayerVisible(false);
        }
    },

    // --- Processing ---
    async processActivePaper() {
        const paper = this.state.activePaper;
        if (!paper) return;

        const mode = UI.$('process-mode').value;
        UI.$('btn-process').disabled = true;

        await API.startProcessing(paper.id, mode);

        API.streamProcessing(paper.id, mode, async (data) => {
            UI.showProgress('llm', data);

            if (data.status === 'completed') {
                UI.$('btn-process').disabled = false;
                const processed = await API.getProcessed(paper.id, mode);
                if (processed) {
                    this.state.displayedSections = processed;
                    this.state.processedMode = mode;
                    UI.renderSections(processed, -1);
                }
            } else if (data.status === 'failed') {
                UI.$('btn-process').disabled = false;
                alert('Processing failed: ' + data.message);
            }
        });
    },

    // --- TTS ---
    async generateTTS() {
        const paper = this.state.activePaper;
        if (!paper) return;

        const voice = UI.$('voice-select').value;
        const speed = parseFloat(UI.$('tts-speed').value);

        UI.$('btn-generate-tts').disabled = true;

        await API.startTTS(paper.id, voice, speed);

        API.streamTTS(paper.id, (data) => {
            UI.showProgress('tts', data);

            // Progressive: show player as soon as first chunk is ready
            if (data.current_chunk >= 1 && this.state.chunks.length === 0) {
                this.refreshChunks();
            }

            if (data.status === 'completed') {
                UI.$('btn-generate-tts').disabled = false;
                this.refreshChunks();
            } else if (data.status === 'failed') {
                UI.$('btn-generate-tts').disabled = false;
                alert('TTS failed: ' + data.message);
            }
        });
    },

    async refreshChunks() {
        const paper = this.state.activePaper;
        if (!paper) return;
        const chunks = await API.listChunks(paper.id);
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
            this.mixer.pauseMusic();
            this.state.playing = false;
        } else {
            if (this.mixer.speechEl.src && !this.mixer.speechEl.ended) {
                await this.mixer.resumeSpeech();
            } else {
                await this.playChunk(this.state.currentChunk);
            }
            this.mixer.playMusic();
            this.state.playing = true;
        }
        UI.updatePlayButton(this.state.playing);
    },

    async playChunk(index) {
        if (index < 0 || index >= this.state.chunks.length) {
            this.state.playing = false;
            UI.updatePlayButton(false);
            this.mixer.pauseMusic();
            return;
        }

        this.state.currentChunk = index;
        const chunk = this.state.chunks[index];
        const url = API.chunkURL(this.state.activePaper.id, chunk.filename);
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

    // --- Music ---
    async loadMusic() {
        this.state.musicList = await API.listMusic();
        UI.renderMusicList(
            this.state.musicList, this.state.activeMusic?.id,
            (m) => this.selectMusic(m), (id) => this.removeMusic(id),
        );
    },

    async uploadMusic(file) {
        try {
            const meta = await API.uploadMusic(file);
            this.state.musicList.push(meta);
            UI.renderMusicList(
                this.state.musicList, this.state.activeMusic?.id,
                (m) => this.selectMusic(m), (id) => this.removeMusic(id),
            );
            this.selectMusic(meta);
        } catch (e) {
            alert('Music upload failed: ' + e.message);
        }
    },

    selectMusic(music) {
        this.state.activeMusic = music;
        this.mixer.setMusic(API.musicURL(music.id));
        UI.renderMusicList(
            this.state.musicList, music.id,
            (m) => this.selectMusic(m), (id) => this.removeMusic(id),
        );
    },

    async removeMusic(id) {
        await API.deleteMusic(id);
        this.state.musicList = this.state.musicList.filter(m => m.id !== id);
        if (this.state.activeMusic?.id === id) {
            this.state.activeMusic = null;
            this.mixer.musicEl.src = '';
        }
        UI.renderMusicList(
            this.state.musicList, this.state.activeMusic?.id,
            (m) => this.selectMusic(m), (id) => this.removeMusic(id),
        );
    },

    // --- Export ---
    async exportMix() {
        const paper = this.state.activePaper;
        if (!paper) return;

        UI.$('btn-export').disabled = true;
        UI.$('btn-export').textContent = 'Exporting...';

        try {
            const speechVol = parseFloat(UI.$('vol-speech').value);
            const musicVol = parseFloat(UI.$('vol-music').value);
            const blob = await API.exportMix(
                paper.id, this.state.activeMusic?.id, speechVol, musicVol,
            );

            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${paper.filename.replace('.pdf', '')}_mixed.mp3`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            alert('Export failed: ' + e.message);
        }

        UI.$('btn-export').disabled = false;
        UI.$('btn-export').textContent = 'Download Mixed MP3';
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
