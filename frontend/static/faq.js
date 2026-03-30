// FAQ accordion — toggle open/close on question click
document.querySelectorAll('.faq-question').forEach(function(btn) {
    btn.addEventListener('click', function() {
        var targetId = this.getAttribute('data-target');
        var answer = document.getElementById(targetId);
        var isExpanded = this.getAttribute('aria-expanded') === 'true';

        // Close all others in the same group
        var group = this.closest('.faq-group');
        group.querySelectorAll('.faq-question').forEach(function(otherBtn) {
            if (otherBtn !== btn) {
                otherBtn.setAttribute('aria-expanded', 'false');
                var otherId = otherBtn.getAttribute('data-target');
                var otherAnswer = document.getElementById(otherId);
                otherAnswer.style.maxHeight = '0';
            }
        });

        // Toggle this one
        if (isExpanded) {
            this.setAttribute('aria-expanded', 'false');
            answer.style.maxHeight = '0';
        } else {
            this.setAttribute('aria-expanded', 'true');
            answer.style.maxHeight = answer.scrollHeight + 'px';
        }
    });

    // Keyboard support
    btn.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            this.click();
        }
    });
});

// Sidebar active state on scroll
var categories = document.querySelectorAll('.faq-category');
var sidebarLinks = document.querySelectorAll('.sidebar-link');

function updateActiveLink() {
    var scrollY = window.scrollY + 120;
    var activeId = null;

    categories.forEach(function(section) {
        if (section.offsetTop <= scrollY) {
            activeId = section.id;
        }
    });

    sidebarLinks.forEach(function(link) {
        var href = link.getAttribute('href').replace('#', '');
        if (href === activeId) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });
}

window.addEventListener('scroll', updateActiveLink, { passive: true });
updateActiveLink();

// Smooth scroll for sidebar links
sidebarLinks.forEach(function(link) {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        var targetId = this.getAttribute('href').replace('#', '');
        var target = document.getElementById(targetId);
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
});
