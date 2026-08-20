import { renderSearch } from './screens/search.js';
import { renderSetup } from './screens/setup.js';
import { renderWorkbench } from './screens/workbench.js';

const routes = {
  search: renderSearch,
  setup: renderSetup,
  workbench: renderWorkbench,
};

const app = document.querySelector('#app');
let teardownRoute = () => {};

export function navigate(params) {
  const nextUrl = new URL(window.location.href);
  nextUrl.search = new URLSearchParams(params).toString();
  window.history.pushState({}, '', nextUrl);
  renderRoute({ focusHeading: true });
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function renderRoute({ focusHeading = false } = {}) {
  const params = new URLSearchParams(window.location.search);
  const routeName = routes[params.get('screen')] ? params.get('screen') : 'search';
  teardownRoute();
  app.replaceChildren();
  teardownRoute = routes[routeName](app, { navigate, params }) ?? (() => {});
  document.title = `${routeName === 'search' ? '开始研究' : routeName === 'setup' ? '设置研究' : '研究工作台'} · 公司投资研究`;

  if (focusHeading) {
    const heading = app.querySelector('h1');
    heading?.setAttribute('tabindex', '-1');
    heading?.focus({ preventScroll: true });
  }
}

window.addEventListener('popstate', () => renderRoute({ focusHeading: true }));
renderRoute();
