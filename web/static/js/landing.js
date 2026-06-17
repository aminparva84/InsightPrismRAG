/* PrismRAG — Landing page interactions */

const nav = document.getElementById('nav');
const navToggle = document.getElementById('nav-toggle');
const navMobile = document.getElementById('nav-mobile');

// ── Nav scroll effect ──────────────────────────────────────────────────────
window.addEventListener('scroll', () => {
  nav.classList.toggle('scrolled', window.scrollY > 20);
}, { passive: true });

// ── Mobile menu ────────────────────────────────────────────────────────────
if (navToggle && navMobile) {
  navToggle.addEventListener('click', () => {
    const open = navMobile.classList.toggle('open');
    navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    navMobile.setAttribute('aria-hidden', open ? 'false' : 'true');
  });
  navMobile.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', () => {
      navMobile.classList.remove('open');
      navToggle.setAttribute('aria-expanded', 'false');
      navMobile.setAttribute('aria-hidden', 'true');
    });
  });
}

// ── Code tab switcher ──────────────────────────────────────────────────────
const snippets = {
  graph: `<span class="c-comment"># Your mapping — not document statistics</span>
<span class="c-keyword">POST</span> /api/prismrag/jobs

{
  <span class="c-key">"strategy"</span>: <span class="c-str">"mlp"</span>,
  <span class="c-key">"mapping"</span>: {
    <span class="c-key">"categories"</span>: [
      { <span class="c-key">"slug"</span>: <span class="c-str">"risk"</span>,   <span class="c-key">"label"</span>: <span class="c-str">"Risk &amp; Compliance"</span> },
      { <span class="c-key">"slug"</span>: <span class="c-str">"growth"</span>, <span class="c-key">"label"</span>: <span class="c-str">"Growth"</span> }
    ],
    <span class="c-key">"rules"</span>: [
      { <span class="c-key">"word"</span>: <span class="c-str">"volatility"</span>, <span class="c-key">"category_slug"</span>: <span class="c-str">"risk"</span> }
    ]
  }
}`,
  delib: `<span class="c-comment"># One call — full 3-phase deliberation pipeline</span>
<span class="c-keyword">POST</span> /api/deliberation/sessions

{
  <span class="c-key">"question"</span>:     <span class="c-str">"Should we acquire CompetitorX in Q4?"</span>,
  <span class="c-key">"domain_count"</span>: 7,
  <span class="c-key">"tenant_id"</span>:    <span class="c-str">"your-workspace"</span>
}

<span class="c-comment">// Returns agreements, conflicts, unique insights, final answer</span>
{
  <span class="c-key">"agreements"</span>:      <span class="c-str">"Finance &amp; Strategy see 12–18% synergy..."</span>,
  <span class="c-key">"conflicts"</span>:       <span class="c-str">"Antitrust: 40% block probability"</span>,
  <span class="c-key">"final_answer"</span>:    <span class="c-str">"Strong fit, material regulatory risk..."</span>,
  <span class="c-key">"confidence"</span>:      0.81
}`
};

document.querySelectorAll('.code-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const key = tab.dataset.tab;
    const snippet = document.getElementById('code-snippet') || document.querySelector('.code-snippet');
    if (snippet && key && snippets[key]) snippet.innerHTML = snippets[key];
  });
});

// ── Intersection observer: fade-in sections ───────────────────────────────
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.style.opacity = '1';
      entry.target.style.transform = 'translateY(0)';
    }
  });
}, { threshold: 0.08 });

document.querySelectorAll(
  '.step-card, .feature-card, .pricing-card, .compare-card, .product-card, .use-case-card, .ml-card, .stat-card'
).forEach(el => {
  el.style.opacity = '0';
  el.style.transform = 'translateY(24px)';
  el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
  observer.observe(el);
});

// ── Pricing: handle plan selection + Stripe redirect ─────────────────────
document.querySelectorAll('.plan-btn[data-plan]').forEach(btn => {
  btn.addEventListener('click', async (e) => {
    const plan = btn.dataset.plan;
    if (!plan || plan === 'free') return;

    const token = localStorage.getItem('prismrag_token');
    if (!token) {
      window.location.href = `/register.html?plan=${plan}`;
      return;
    }

    btn.textContent = 'Redirecting…';
    btn.style.opacity = '0.7';

    try {
      const res = await fetch('/api/billing/checkout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ plan }),
      });
      const data = await res.json();
      if (data.redirect) window.location.href = data.redirect;
    } catch {
      btn.textContent = 'Try again';
      btn.style.opacity = '1';
    }
  });
});
