if (!ENVIRONMENT_IS_PTHREAD && typeof window !== 'undefined') {
	// Typing into the game shouldn't also scroll or type into the page.
	Module['canvas']?.addEventListener('keypress', e => e.preventDefault())
}
