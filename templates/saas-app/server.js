const express = require('express');
const crypto = require('crypto');
const bcrypt = require('bcrypt');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

const SALT_ROUNDS = 10;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// In-memory store (agents can wire to real DB later)
const users = [];
const sessions = {};

// Auth: signup
app.post('/api/auth/signup', async (req, res) => {
    const { email, password } = req.body;
    if (!email || !password) {
        return res.status(400).json({ error: 'Email and password required' });
    }
    if (users.find(u => u.email === email)) {
        return res.status(409).json({ error: 'Email already registered' });
    }
    const password_hash = await bcrypt.hash(password, SALT_ROUNDS);
    const user = { id: users.length + 1, email, password_hash, created_at: new Date().toISOString() };
    users.push(user);
    const token = crypto.randomUUID();
    sessions[token] = user.id;
    res.status(201).json({ token, user: { id: user.id, email: user.email } });
});

// Auth: login
app.post('/api/auth/login', async (req, res) => {
    const { email, password } = req.body;
    const user = users.find(u => u.email === email);
    if (!user || !await bcrypt.compare(password || '', user.password_hash)) {
        return res.status(401).json({ error: 'Invalid credentials' });
    }
    const token = crypto.randomUUID();
    sessions[token] = user.id;
    res.json({ token, user: { id: user.id, email: user.email } });
});

// Auth middleware
function requireAuth(req, res, next) {
    const token = (req.headers.authorization || '').replace('Bearer ', '');
    const userId = sessions[token];
    if (!userId) return res.status(401).json({ error: 'Unauthorized' });
    req.userId = userId;
    next();
}

// Dashboard data (populated by agents)
app.get('/api/dashboard', requireAuth, (req, res) => {
    res.json({
        user_id: req.userId,
        metrics: { views: 0, conversions: 0, revenue: 0 },
        recent_activity: [],
    });
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok', users: users.length });
});

app.listen(PORT, () => {
    console.log(`SaaS app running on port ${PORT}`);
});
