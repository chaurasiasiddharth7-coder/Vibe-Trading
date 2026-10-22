/** Set at build time for the hosted dashboard: only the dashboard works there (its backend serves only those routes). */
export const DASHBOARD_ONLY = import.meta.env.VITE_DASHBOARD_ONLY === "1";
