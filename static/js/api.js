const API = {
    async uploadPaper(file) {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/papers/upload', { method: 'POST', body: form });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    },

    async listPapers() {
        const res = await fetch('/api/papers');
        return res.json();
    },

    async getPaper(id) {
        const res = await fetch(`/api/papers/${id}`);
        return res.json();
    },

    async startProcessing(paperId, mode) {
        const res = await fetch(`/api/papers/${paperId}/process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        return res.json();
    },

    streamProcessing(paperId, mode, onEvent) {
        return this._sse(`/api/papers/${paperId}/process/stream?mode=${mode}`, onEvent);
    },

    async getProcessed(paperId, mode) {
        const res = await fetch(`/api/papers/${paperId}/processed/${mode}`);
        if (!res.ok) return null;
        return res.json();
    },

    async startTTS(paperId, voice, speed) {
        const res = await fetch('/api/tts/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paper_id: paperId, voice, speed }),
        });
        return res.json();
    },

    streamTTS(paperId, onEvent) {
        return this._sse(`/api/tts/${paperId}/stream`, onEvent);
    },

    async listChunks(paperId) {
        const res = await fetch(`/api/tts/${paperId}/chunks`);
        return res.json();
    },

    chunkURL(paperId, filename) {
        return `/api/tts/${paperId}/${filename}`;
    },

    async uploadMusic(file) {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/music/upload', { method: 'POST', body: form });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    },

    async listMusic() {
        const res = await fetch('/api/music');
        return res.json();
    },

    musicURL(musicId) {
        return `/api/music/${musicId}`;
    },

    async deleteMusic(musicId) {
        await fetch(`/api/music/${musicId}`, { method: 'DELETE' });
    },

    async generateMusic(prompt, duration) {
        const res = await fetch('/api/music/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt, duration }),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    },

    streamMusicGen(taskId, onEvent) {
        return this._sse(`/api/music/generate/stream?task_id=${taskId}`, onEvent);
    },

    async exportMix(paperId, musicId, speechVol, musicVol) {
        const res = await fetch('/api/mix/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                paper_id: paperId,
                music_id: musicId,
                speech_volume: speechVol,
                music_volume: musicVol,
            }),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.blob();
    },

    _sse(url, onEvent) {
        const es = new EventSource(url);
        es.onmessage = (e) => {
            const data = JSON.parse(e.data);
            onEvent(data);
            if (data.status === 'completed' || data.status === 'failed' || data.status === 'not_found') {
                es.close();
            }
        };
        es.onerror = () => es.close();
        return es;
    },
};
