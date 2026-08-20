export function renderWorkbench(root, { params }) {
  const variant = params.get('variant') === 'C' ? 'C' : 'A';
  const security = variant === 'C' ? 'GOOG Class C' : 'GOOGL Class A';

  root.innerHTML = `
    <main class="workbench-placeholder" id="main-content">
      <a class="back-link" href="?screen=setup&security=${variant === 'C' ? 'GOOG' : 'GOOGL'}">
        <span aria-hidden="true">←</span> 返回研究设置
      </a>
      <section aria-labelledby="workbench-title">
        <p class="eyebrow">Alphabet · ${security}</p>
        <h1 id="workbench-title">研究工作台</h1>
        <p>研究对象、问题与视角已经就位。工作台内容将在下一阶段展开。</p>
      </section>
    </main>
  `;
}
