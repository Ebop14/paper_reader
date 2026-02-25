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

    async deletePaper(id) {
        const res = await fetch(`/api/papers/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    },

    // --- Pipeline ---

    async startPipeline(paperId, voice, speed) {
        const res = await fetch(`/api/pipeline/${paperId}/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice, speed }),
        });
        return res.json();
    },

    streamPipeline(paperId, onEvent) {
        return this._sse(`/api/pipeline/${paperId}/stream`, onEvent);
    },

    async startRender(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/render`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        return res.json();
    },

    streamRender(paperId, onEvent) {
        return this._sse(`/api/pipeline/${paperId}/render/stream`, onEvent);
    },

    async startReannotate(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/reannotate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        return res.json();
    },

    streamReannotate(paperId, onEvent) {
        return this._sse(`/api/pipeline/${paperId}/reannotate/stream`, onEvent);
    },

    async getScript(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/script`);
        if (!res.ok) return null;
        return res.json();
    },

    async listPipelineAudio(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/audio`);
        return res.json();
    },

    pipelineAudioURL(paperId, filename) {
        return `/api/pipeline/${paperId}/audio/${filename}`;
    },

    // --- Legacy TTS (backward compat) ---

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

    // --- Animations ---

    async listAnimations(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/animations`);
        return res.json();
    },

    animationURL(paperId, filename) {
        return `/api/pipeline/${paperId}/animations/${filename}`;
    },

    videoURL(paperId) {
        return `/api/pipeline/${paperId}/video`;
    },

    async exportVideo(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/export-video`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error(await res.text());
        return res.blob();
    },

    // --- Export ---

    async exportVoiceover(paperId) {
        const res = await fetch(`/api/pipeline/${paperId}/export`, {
            method: 'POST',
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
