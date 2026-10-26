// Shared form validation for quote.html and index.html.
// The backend (app.py) runs the same checks, since anything in the browser can be bypassed.

function isValidEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(value);
}

function isValidPhone(value) {
    let digits = value.replace(/\D/g, '');
    if (digits.length === 11 && digits.startsWith('1')) digits = digits.slice(1);
    // US numbers: area code and exchange can't start with 0 or 1
    return /^[2-9]\d{2}[2-9]\d{6}$/.test(digits);
}

function isValidDob(value) {
    const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
    if (!match) return false;
    const month = Number(match[1]);
    const day = Number(match[2]);
    const year = Number(match[3]);
    const date = new Date(year, month - 1, day);
    // Rejects impossible dates like 02/31 (Date rolls them over)
    if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return false;
    const today = new Date();
    const oldest = new Date(today.getFullYear() - 120, today.getMonth(), today.getDate());
    return date <= today && date >= oldest;
}

function isValidZip(value) {
    return /^\d{5}$/.test(value);
}

// fields: [{ input, error, isValid }]. Shows errors on blur and hides them while typing.
function attachFieldValidation(fields) {
    fields.forEach((field) => {
        field.input.addEventListener('blur', () => {
            if (field.input.value.trim()) showFieldState(field, field.isValid(field.input.value.trim()));
        });
        field.input.addEventListener('input', () => showFieldState(field, true));
    });
}

function showFieldState({ input, error }, valid) {
    error.style.display = valid ? 'none' : 'block';
    input.style.borderColor = valid ? '' : '#e74c3c';
}

// Checks every field, shows errors, focuses the first bad one. Returns true if all are valid.
function validateFields(fields) {
    let firstInvalid = null;
    fields.forEach((field) => {
        const valid = field.isValid(field.input.value.trim());
        showFieldState(field, valid);
        if (!valid && !firstInvalid) firstInvalid = field.input;
    });
    if (firstInvalid) firstInvalid.focus();
    return !firstInvalid;
}

// Formats as (555) 123-4567 while typing; drops a leading country code 1
function attachPhoneFormatting(input) {
    input.addEventListener('input', () => {
        const digits = input.value.replace(/\D/g, '').replace(/^1/, '').slice(0, 10);
        if (digits.length > 6) input.value = `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
        else if (digits.length > 3) input.value = `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
        else input.value = digits;
    });
}

// Inserts slashes as MM/DD/YYYY while typing
function attachDateFormatting(input) {
    input.addEventListener('input', (e) => {
        if (e.inputType && e.inputType.startsWith('delete')) return;
        const digits = input.value.replace(/\D/g, '').slice(0, 8);
        if (digits.length > 4) input.value = `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
        else if (digits.length > 2) input.value = `${digits.slice(0, 2)}/${digits.slice(2)}`;
        else input.value = digits;
    });
}
