document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navBtns = document.querySelectorAll('.nav-btn[data-target]');
    const sections = document.querySelectorAll('section.glass-panel');

    navBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            navBtns.forEach(b => b.classList.remove('active'));
            sections.forEach(s => s.classList.remove('active'));

            btn.classList.add('active');
            document.getElementById(btn.dataset.target).classList.add('active');
        });
    });

    // Determine backend API Base URL:
    // If frontend is running on a static dev server (such as Live Server on port 5500, or file:),
    // redirect API requests to the Python Flask backend on port 5000.
    // When served directly via Flask (port 5000) or deployed to Vercel/production, use relative path ('').
    const isStaticDevServer = (['5500', '5501', '3000', '8080'].includes(window.location.port) || window.location.protocol === 'file:');
    const API_BASE = isStaticDevServer ? 'http://127.0.0.1:5000' : '';

    // Extractor Logic
    const analyzeBtn = document.getElementById('analyze-btn');
    const urlInput = document.getElementById('url-input');
    const inputGroup = document.querySelector('.input-group');
    
    const analyzingState = document.getElementById('analyzing-state');
    const resultsState = document.getElementById('results-state');
    const processingState = document.getElementById('processing-state');
    const completedState = document.getElementById('completed-state');
    
    const analyzeLog = document.getElementById('analyze-log');
    const processLog = document.getElementById('process-log');
    
    const actionBtns = document.querySelectorAll('.action-btn');
    const resetBtn = document.getElementById('reset-btn');

    let currentUrl = '';

    // Utility for logging
    function addLog(container, message) {
        const line = document.createElement('div');
        line.className = 'log-line';
        const time = new Date().toISOString().split('T')[1].substring(0,8);
        line.innerHTML = `<span style="color:var(--text-muted)">[${time}]</span> ${message}`;
        container.appendChild(line);
        container.scrollTop = container.scrollHeight;
    }

    analyzeBtn.addEventListener('click', async () => {
        const url = urlInput.value.trim();
        if (!url) return;
        currentUrl = url;

        // Transition to analyzing
        inputGroup.classList.add('hidden');
        analyzingState.classList.remove('hidden');
        analyzeLog.innerHTML = '';

        addLog(analyzeLog, `INITIALIZING EXTRACTOR PROTOCOL...`);
        addLog(analyzeLog, `TARGET URL: ${url}`);
        addLog(analyzeLog, `FETCHING REALTIME METADATA via yt-dlp...`);
        
        try {
            const response = await fetch(`${API_BASE}/api/analyze`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            });

            if (!response.ok) {
                let errorMsg = `Server returned status ${response.status}`;
                try {
                    const errorJson = await response.json();
                    if (errorJson && errorJson.error) errorMsg = errorJson.error;
                } catch (e) {
                    if (response.status === 405) {
                        errorMsg = "Method Not Allowed (405). Please start the backend with: python api/index.py";
                    }
                }
                addLog(analyzeLog, `ERROR: ${errorMsg}`);
                setTimeout(() => {
                    analyzingState.classList.add('hidden');
                    inputGroup.classList.remove('hidden');
                }, 4000);
                return;
            }

            const data = await response.json();
            
            addLog(analyzeLog, `METADATA ACQUIRED SUCCESSFULLY.`);
            document.getElementById('media-title').innerText = data.title;
            document.getElementById('media-thumb').src = data.thumbnail;
            document.getElementById('media-duration').innerText = data.duration;
            document.getElementById('media-source').innerText = data.source;

            setTimeout(() => {
                analyzingState.classList.add('hidden');
                resultsState.classList.remove('hidden');
            }, 1000);

        } catch (err) {
            const isConnectionError = err.message.includes('Failed to fetch') || err.message.includes('NetworkError');
            if (isConnectionError) {
                addLog(analyzeLog, `BACKEND OFFLINE: Python server unreachable at ${API_BASE || window.location.origin}.`);
                addLog(analyzeLog, `Please start the backend server: python api/index.py`);
            } else {
                addLog(analyzeLog, `NETWORK ERROR: ${err.message}`);
            }
            setTimeout(() => {
                analyzingState.classList.add('hidden');
                inputGroup.classList.remove('hidden');
            }, 4000);
        }
    });

    actionBtns.forEach(btn => {
        btn.addEventListener('click', async () => {
            const type = btn.dataset.type;
            
            resultsState.classList.add('hidden');
            processingState.classList.remove('hidden');
            processLog.innerHTML = '';
            
            const barFill = document.getElementById('extraction-progress');
            const percentText = document.getElementById('progress-percent');
            const speedText = document.getElementById('progress-speed');
            const etaText = document.getElementById('progress-eta');

            addLog(processLog, `COMMENCING ${type.toUpperCase()} EXTRACTION...`);
            addLog(processLog, `SPAWNING YT-DLP SUBPROCESS...`);

            try {
                const response = await fetch(`${API_BASE}/api/download`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: currentUrl, type: type })
                });

                if (!response.ok) {
                    addLog(processLog, `ERROR: Server returned status ${response.status}`);
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder("utf-8");

                let buffer = "";

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n\n');
                    buffer = lines.pop(); // keep incomplete chunk in buffer

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const msg = JSON.parse(line.substring(6));
                                
                                if (msg.type === 'progress') {
                                    let prog = parseFloat(msg.progress);
                                    if (!isNaN(prog)) {
                                        barFill.style.width = `${prog}%`;
                                        percentText.innerText = `${prog.toFixed(1)}%`;
                                    }
                                    speedText.innerText = msg.speed;
                                    etaText.innerText = `ETA: ${msg.eta}`;
                                } else if (msg.type === 'log') {
                                    addLog(processLog, msg.message);
                                } else if (msg.type === 'error') {
                                    addLog(processLog, `CRITICAL ERROR: ${msg.message}`);
                                } else if (msg.type === 'done') {
                                    addLog(processLog, `EXTRACTION COMPLETE.`);
                                    barFill.style.width = `100%`;
                                    percentText.innerText = `100%`;
                                    
                                    if (msg.filename) {
                                        const dlUrl = `${API_BASE}/api/serve_file/${msg.dl_type}/${encodeURIComponent(msg.filename)}`;
                                        const a = document.createElement('a');
                                        a.href = dlUrl;
                                        a.download = msg.filename;
                                        document.body.appendChild(a);
                                        a.click();
                                        document.body.removeChild(a);
                                    }

                                    setTimeout(() => {
                                        processingState.classList.add('hidden');
                                        completedState.classList.remove('hidden');
                                    }, 1500);
                                }
                            } catch (e) {
                                console.error("Error parsing chunk", line);
                            }
                        }
                    }
                }
            } catch (err) {
                const isConnectionError = err.message.includes('Failed to fetch') || err.message.includes('NetworkError');
                if (isConnectionError) {
                    addLog(processLog, `BACKEND OFFLINE: Python server unreachable.`);
                    addLog(processLog, `Run: python api/index.py`);
                } else {
                    addLog(processLog, `NETWORK ERROR: ${err.message}`);
                }
            }
        });
    });

    resetBtn.addEventListener('click', () => {
        completedState.classList.add('hidden');
        inputGroup.classList.remove('hidden');
        urlInput.value = '';
        urlInput.focus();
        document.getElementById('extraction-progress').style.width = '0%';
    });

    // Legacy Terminal
    const legacyBtn = document.getElementById('legacy-btn');
    const legacyTerm = document.getElementById('legacy-terminal');
    const closeTerm = document.getElementById('close-terminal');
    const termInput = document.getElementById('term-input');
    const termBody = document.getElementById('term-body');

    legacyBtn.addEventListener('click', () => {
        legacyTerm.classList.remove('hidden');
        termInput.focus();
    });

    closeTerm.addEventListener('click', () => {
        legacyTerm.classList.add('hidden');
    });

    let termStep = 0;
    let legacyUrl = '';
    termInput.addEventListener('keydown', async (e) => {
        if (e.key === 'Enter') {
            const val = termInput.value;
            const div = document.createElement('div');
            div.className = 'term-line';
            div.innerText = `> ${val}`;
            termBody.insertBefore(div, termInput.parentElement);
            termInput.value = '';

            if (termStep === 0) {
                legacyUrl = val;
                const choices = document.createElement('div');
                choices.innerHTML = `<br>1. Download Video<br>2. Download Audio<br><br>Choice: `;
                termBody.insertBefore(choices, termInput.parentElement);
                termStep = 1;
            } else if (termStep === 1) {
                const choice = val.trim();
                const type = choice === '2' ? 'audio' : 'video';
                
                const proc = document.createElement('div');
                proc.innerHTML = `<br>Processing...<br>`;
                termBody.insertBefore(proc, termInput.parentElement);
                termInput.disabled = true;

                try {
                    const response = await fetch(`${API_BASE}/api/download`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: legacyUrl, type: type })
                    });
                    
                    if (!response.ok) {
                        throw new Error(`Server status ${response.status}`);
                    }

                    const reader = response.body.getReader();
                    const decoder = new TextDecoder("utf-8");
                    let buffer = "";

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n\n');
                        buffer = lines.pop();

                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                const msg = JSON.parse(line.substring(6));
                                if (msg.type === 'log') {
                                    const logDiv = document.createElement('div');
                                    logDiv.innerText = msg.message;
                                    termBody.insertBefore(logDiv, termInput.parentElement);
                                    termBody.scrollTop = termBody.scrollHeight;
                                } else if (msg.type === 'done') {
                                    const doneDiv = document.createElement('div');
                                    doneDiv.innerHTML = `<br>Download Complete.<br><br>Paste URL: `;
                                    termBody.insertBefore(doneDiv, termInput.parentElement);
                                    termInput.disabled = false;
                                    termInput.focus();
                                    termStep = 0;
                                    termBody.scrollTop = termBody.scrollHeight;

                                    if (msg.filename) {
                                        const dlUrl = `${API_BASE}/api/serve_file/${msg.dl_type}/${encodeURIComponent(msg.filename)}`;
                                        const a = document.createElement('a');
                                        a.href = dlUrl;
                                        a.download = msg.filename;
                                        document.body.appendChild(a);
                                        a.click();
                                        document.body.removeChild(a);
                                    }
                                } else if (msg.type === 'error') {
                                    const errDiv = document.createElement('div');
                                    errDiv.innerHTML = `<br>Error: ${msg.message}<br><br>Paste URL: `;
                                    termBody.insertBefore(errDiv, termInput.parentElement);
                                    termInput.disabled = false;
                                    termInput.focus();
                                    termStep = 0;
                                    termBody.scrollTop = termBody.scrollHeight;
                                }
                            }
                        }
                    }
                } catch (err) {
                    const isConnectionError = err.message.includes('Failed to fetch') || err.message.includes('NetworkError');
                    const msg = isConnectionError 
                        ? "Backend offline. Please run 'python api/index.py'." 
                        : err.message;
                    const errDiv = document.createElement('div');
                    errDiv.innerHTML = `<br>Error: ${msg}<br><br>Paste URL: `;
                    termBody.insertBefore(errDiv, termInput.parentElement);
                    termInput.disabled = false;
                    termInput.focus();
                    termStep = 0;
                }
            }
            termBody.scrollTop = termBody.scrollHeight;
        }
    });
});
