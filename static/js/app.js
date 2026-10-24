const menuButton = document.querySelector('.mobile-menu');
const sidebar = document.querySelector('.sidebar');

if (menuButton && sidebar) {
  menuButton.addEventListener('click', () => {
    const expanded = menuButton.getAttribute('aria-expanded') === 'true';
    menuButton.setAttribute('aria-expanded', String(!expanded));
    sidebar.classList.toggle('mobile-open', !expanded);
  });

  document.addEventListener('click', (event) => {
    if (window.innerWidth <= 640 && !sidebar.contains(event.target) && !menuButton.contains(event.target)) {
      sidebar.classList.remove('mobile-open');
      menuButton.setAttribute('aria-expanded', 'false');
    }
  });
}

const complaintForm = document.querySelector('form[enctype="multipart/form-data"]');

if (complaintForm) {
  complaintForm.addEventListener('submit', (event) => {
    const email = complaintForm.elements.student_email.value.trim();
    const studentId = complaintForm.elements.student_id.value.trim();
    const attachment = complaintForm.elements.attachment.files[0];
    const emailOrId = complaintForm.elements.student_email;

    if (!email) {
      event.preventDefault();
      emailOrId.setCustomValidity('Enter your college email to submit and track this complaint.');
      emailOrId.reportValidity();
      return;
    }
    emailOrId.setCustomValidity('');

    if (attachment && attachment.size > 5 * 1024 * 1024) {
      event.preventDefault();
      attachmentFormMessage(complaintForm, 'The image must be 5 MB or smaller.');
      return;
    }
    if (attachment && !['image/jpeg', 'image/png', 'image/webp'].includes(attachment.type)) {
      event.preventDefault();
      attachmentFormMessage(complaintForm, 'Choose a JPG, PNG, or WebP image.');
    }
  });
}

function attachmentFormMessage(form, message) {
  const field = form.elements.attachment;
  field.setCustomValidity(message);
  field.reportValidity();
  field.addEventListener('change', () => field.setCustomValidity(''), { once: true });
}