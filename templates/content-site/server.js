const express = require('express');
const path = require('path');
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Newsletter signup
const subscribers = [];
app.post('/api/subscribe', (req, res) => {
    const { email } = req.body;
    if (!email || !email.includes('@')) {
        return res.status(400).json({ error: 'Valid email required' });
    }
    if (subscribers.includes(email)) {
        return res.json({ message: 'Already subscribed' });
    }
    subscribers.push(email);
    console.log(`New subscriber: ${email} (total: ${subscribers.length})`);
    res.json({ message: 'Subscribed successfully' });
});

// Blog posts API (populated by Arclane agents)
const posts = [];
app.get('/api/posts', (req, res) => {
    res.json(posts);
});

app.post('/api/posts', (req, res) => {
    const { title, body, author } = req.body;
    if (!title || !body) {
        return res.status(400).json({ error: 'Title and body required' });
    }
    const post = {
        id: posts.length + 1,
        title,
        body,
        author: author || 'AI',
        created_at: new Date().toISOString(),
    };
    posts.push(post);
    res.status(201).json(post);
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'ok', subscribers: subscribers.length, posts: posts.length });
});

app.listen(PORT, () => {
    console.log(`Content site running on port ${PORT}`);
});
