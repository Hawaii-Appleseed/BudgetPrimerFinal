// Simple client-side router
class Router {
    constructor(routes) {
        this.routes = routes || [];
        this.rootElement = document.getElementById('app');
        // Don't auto-initialize, let the caller control when to init
        this.init();
    }

    async init() {
        // Set initial loading state
        this.rootElement.innerHTML = `
            <div class="loading">
                <div class="spinner"></div>
                <p>Loading budget data...</p>
            </div>`;
        
        try {
            // Load all data sources in parallel
            const loadPromises = [];

            if (window.loadDepartments) {
                loadPromises.push(window.loadDepartments());
            }
            if (window.loadSummaryStats) {
                loadPromises.push(window.loadSummaryStats());
            }
            if (window.loadPrograms) {
                loadPromises.push(window.loadPrograms());
            }
            if (window.loadFYComparison) {
                loadPromises.push(window.loadFYComparison());
            }
            if (window.loadGovernorRequest) {
                loadPromises.push(window.loadGovernorRequest());
            }
            if (window.loadDraftComparison) {
                loadPromises.push(window.loadDraftComparison());
            }
            if (window.loadProgramPurposes) {
                loadPromises.push(window.loadProgramPurposes());
            }
            if (window.loadProjects) {
                loadPromises.push(window.loadProjects());
            }
            if (window.loadHistoricalTrends) {
                loadPromises.push(window.loadHistoricalTrends());
            }
            if (window.loadByDeptDatasets) {
                loadPromises.push(window.loadByDeptDatasets());
            }
            if (window.loadObligatedCosts) {
                loadPromises.push(window.loadObligatedCosts());
            }
            if (window.loadActuals) {
                loadPromises.push(window.loadActuals());
            }
            if (window.loadSchoolFoodService) {
                loadPromises.push(window.loadSchoolFoodService());
            }
            if (window.loadCountyBudgets) {
                loadPromises.push(window.loadCountyBudgets());
            }

            // Wait for both to complete
            await Promise.all(loadPromises);
            
            // Set up event listeners
            window.addEventListener('popstate', () => this.handleRoute());
            
            document.addEventListener('click', (e) => {
                const link = e.target.closest('a');
                if (link && link.getAttribute('href')?.startsWith('#')) {
                    e.preventDefault();
                    const path = link.getAttribute('href');
                    window.history.pushState({}, '', path);
                    this.handleRoute();
                }
            });
            
            // Initial route handling
            this.handleRoute();
        } catch (error) {
            console.error('Error initializing router:', error);
            this.rootElement.innerHTML = `
                <div class="error-message">
                    <h2>Error Loading Application</h2>
                    <p>There was an error loading the budget data. Please refresh the page to try again.</p>
                    <p>${error.message}</p>
                </div>`;
        }
    }

    async handleRoute() {
        const fullHash = window.location.hash.slice(1) || '/';
        const qIdx = fullHash.indexOf('?');
        const path = qIdx >= 0 ? fullHash.slice(0, qIdx) : fullHash;
        window._routeQueryString = qIdx >= 0 ? fullHash.slice(qIdx + 1) : '';
        
        // Find matching route (including parameterized routes)
        let route = this.routes.find(r => r.path === path);
        
        // If no exact match, check for parameterized routes
        let params = {};
        if (!route) {
            route = this.routes.find(r => {
                if (r.path.includes(':')) {
                    const routeParts = r.path.split('/');
                    const pathParts = path.split('/');
                    
                    if (routeParts.length !== pathParts.length) return false;
                    
                    const matches = routeParts.every((part, index) => {
                        if (part.startsWith(':')) {
                            // Extract parameter
                            const paramName = part.slice(1);
                            params[paramName] = pathParts[index];
                            return true;
                        }
                        return part === pathParts[index];
                    });
                    
                    return matches;
                }
                return false;
            });
        }
        
        // Fallback to wildcard route
        if (!route) {
            route = this.routes.find(r => r.path === '*');
        }
        
        if (route) {
            try {
                // Show loading state
                this.rootElement.innerHTML = `
                    <div class="loading">
                        <div class="spinner"></div>
                        <p>Loading...</p>
                    </div>`;
                
                // Load and render the component
                const html = await route.component(params);
                this.rootElement.innerHTML = html;
                
                // Initialize any component-specific logic
                if (route.init) {
                    await route.init();
                }
                
                // Update active nav link, scope pill, and header text
                this.updateActiveLink(path);
                if (window.updateScopeToggle) window.updateScopeToggle(path);
                this.updateHeaderText(path);

                // Notify parent if in iframe
                this.notifyParentHeight();
                
            } catch (error) {
                console.error('Error loading route:', error);
                this.rootElement.innerHTML = `
                    <div class="error-message">
                        <h2>Error Loading Page</h2>
                        <p>There was an error loading the requested page. Please try again later.</p>
                        <a href="#/" class="button">Return Home</a>
                    </div>`;
            }
        }
    }
    
    updateHeaderText(path) {
        const configs = {
            '/enacted': {
                subtitle: 'Enacted appropriations by department · FY2016–FY2027. Pick a year; FY2026–27 reflect the enacted supplemental (Act 175).',
                docTitle: 'Hawaiʻi Budget Tracker · By Department'
            },
            '/history': {
                subtitle: 'Enacted appropriations by department · FY2016–FY2027. Pick a year; FY2026–27 reflect the enacted supplemental (Act 175).',
                docTitle: 'Hawaiʻi Budget Tracker · By Department'
            },
            '/actuals': {
                subtitle: 'What was actually spent in FY2025 vs the budget, by department · budgetary basis, appropriated funds. A completed year — separate from the FY2026–27 bills.',
                docTitle: 'Actual Spending · Hawaiʻi Budget Tracker'
            },
            '/school-food-service': {
                subtitle: 'School Food Service revenues vs. expenditures · cash basis, FY2021–FY2025 · HIDOE.',
                docTitle: 'School Food Service · Hawaiʻi Budget Tracker'
            },
            '/about': {
                subtitle: 'About this tracker.',
                docTitle: 'About · Hawaiʻi Budget Tracker'
            }
        };

        const defaults = {
            subtitle: 'FY2026–27 · Compare the Governor, House, and Senate drafts.',
            docTitle: 'Hawaiʻi Budget Tracker · HB1800'
        };

        // County routes are parameterized (/counties/honolulu …) so they're
        // matched by prefix rather than the exact-path config table.
        const isCounties = path.startsWith('/counties');
        const cfg = isCounties
            ? {
                subtitle: 'County operating budgets · what each county government plans to spend.',
                docTitle: 'County Budgets · Hawaiʻi Budget Tracker'
            }
            : (configs[path] || defaults);

        const subtitleEl = document.querySelector('.app-header-subtitle');
        if (subtitleEl) subtitleEl.textContent = cfg.subtitle;
        document.title = cfg.docTitle;

        // Page-specific footer source line. The School Food Service page draws
        // from HIDOE rather than the HB1800 bill, so override the footnote there.
        const footerSourceEl = document.querySelector('.app-footer-source');
        if (footerSourceEl) {
            if (isCounties) {
                footerSourceEl.innerHTML = 'Data Source: county budget documents — Honolulu via <a href="https://data.honolulu.gov" target="_blank" rel="noopener">data.honolulu.gov</a> + capital budget ordinance; Kauaʻi operating &amp; capital budget ordinances; Maui FY2026 Mayor’s Proposed budget; Hawaiʻi FY2026-27 operating budget (Bill 135, by fund)';
            } else if (path === '/school-food-service') {
                footerSourceEl.innerHTML = 'Data Source: <a href="https://hawaiipublicschools.org/data-reports/fiscal/" target="_blank" rel="noopener">HIDOE School Food Services</a> · cash basis, FY2021–FY2025 (as of June 30, 2025)';
            } else {
                footerSourceEl.innerHTML = 'Data Source: <a href="https://www.capitol.hawaii.gov/sessions/session2026/bills/HB1800_CD1_.HTM" target="_blank" rel="noopener">HB1800</a> Supplemental Appropriations, enacted as Act 175 (June 26, 2026)';
            }
        }
    }

    updateActiveLink(currentPath) {
        document.querySelectorAll('.nav-link').forEach(link => {
            const linkPath = link.getAttribute('href').replace('#', '');
            const isActive = linkPath === '/'
                ? currentPath === '/'
                : linkPath !== '' && currentPath.startsWith(linkPath);
            link.classList.toggle('active', isActive);
        });
    }
    
    notifyParentHeight() {
        // Only run if we're inside an iframe
        if (window.self !== window.top) {
            const height = document.documentElement.scrollHeight;
            window.parent.postMessage({ 
                type: 'setHeight', 
                height: height 
            }, '*');
        }
    }
}

// Export the router for use in app.js
window.Router = Router;
