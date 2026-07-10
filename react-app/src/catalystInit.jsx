// src/catalystInit.jsx
// Catalyst Web SDK loads globally via script tags in public/index.html (not npm).
// This just gives the rest of the app a safe reference to it.
const catalyst = typeof window !== "undefined" ? window.catalyst : null;
export default catalyst;
