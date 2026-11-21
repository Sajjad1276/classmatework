import { useState } from "react";

const navigation = [
  { label: "داشبورد", group: "نمای کلی", marker: "01" },
  { label: "سازمان‌ها و مدارس", group: "ساختار", marker: "02" },
  { label: "ساختار آموزشی", group: "ساختار", marker: "03" },
  { label: "کاربران و دسترسی", group: "مدیریت", marker: "04" },
  { label: "محتوا و گردش‌کار", group: "مدیریت", marker: "05" },
  { label: "صف تأیید", group: "مدیریت", marker: "06" },
  { label: "ارتباطات", group: "عملیات", marker: "07" },
  { label: "گزارش‌ها", group: "عملیات", marker: "08" },
  { label: "امنیت و ممیزی", group: "سامانه", marker: "09" },
  { label: "تنظیمات", group: "سامانه", marker: "10" },
];

const groups = ["نمای کلی", "ساختار", "مدیریت", "عملیات", "سامانه"];

export function App() {
  const [activeSection, setActiveSection] = useState("داشبورد");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div>
            <div className="brand-name">ClassMate</div>
            <div className="brand-caption">WORKSPACE / CONTROL</div>
          </div>
        </div>
        <div className="workspace-label">فضای مدیریت</div>
        <nav aria-label="ناوبری اصلی" className="navigation">
          {groups.map((group) => (
            <div className="nav-group" key={group}>
              <div className="nav-heading">{group}</div>
              {navigation.filter((item) => item.group === group).map((item) => (
                <button
                  aria-current={activeSection === item.label ? "page" : undefined}
                  className={"nav-item" + (activeSection === item.label ? " active" : "")}
                  key={item.marker}
                  onClick={() => setActiveSection(item.label)}
                  type="button"
                >
                  <span className="nav-marker">{item.marker}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="footer-indicator" />
          <span>محیط توسعه · فاز صفر</span>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>ClassMate Work</span>
            <span className="breadcrumb-separator">/</span>
            <strong>{activeSection}</strong>
          </div>
          <div className="topbar-status"><span className="status-dot" />زیرساخت اولیه</div>
        </header>

        <section className="page-content">
          <div className="page-eyebrow">OPERATIONS PLATFORM · FOUNDATION</div>
          <div className="page-heading-row">
            <div>
              <h1>{activeSection}</h1>
              <p className="page-subtitle">مرکز یکپارچه‌ی مدیریت سازمان، مدرسه و عملیات ClassMate</p>
            </div>
            <div className="version-label">نسخه ۰.۱</div>
          </div>

          <section className="foundation-panel">
            <div className="panel-decoration" aria-hidden="true"><span /><span /><span /></div>
            <div className="panel-content">
              <div className="panel-kicker"><span className="kicker-line" />PHASE 00 / FOUNDATION</div>
              <h2>زیرساخت سامانه در حال شکل‌گیری است</h2>
              <p>
                مرزهای معماری، API پایه و پوسته‌ی مدیریتی ایجاد شده‌اند. قابلیت‌های
                عملیاتی پس از تکمیل هویت، دسترسی و قرارداد داده به‌صورت مرحله‌ای فعال خواهند شد.
              </p>
              <div className="panel-note">
                <span className="note-symbol">i</span>
                این محیط فعلاً نمای زیرساختی است؛ داده‌ی نمایشی یا عملیات مدیریتی فعال ندارد.
              </div>
            </div>
          </section>

          <div className="section-heading">
            <div>
              <h3>وضعیت اجزای پایه</h3>
              <p>نمایش وضعیت استقرار، نه شاخص‌های ساختگی کسب‌وکار</p>
            </div>
            <span className="section-caption">BOOTSTRAP CHECKLIST</span>
          </div>

          <div className="status-grid">
            <article className="status-card">
              <div className="status-card-top"><span className="status-icon api-icon">API</span><span className="status-tag planned">پایه ایجاد شده</span></div>
              <h4>Backend API</h4><p>FastAPI · قرارداد نسخه‌دار</p>
            </article>
            <article className="status-card">
              <div className="status-card-top"><span className="status-icon data-icon">DB</span><span className="status-tag planned">اتصال پیکربندی شد</span></div>
              <h4>داده و زیرساخت</h4><p>PostgreSQL · Redis · Alembic</p>
            </article>
            <article className="status-card">
              <div className="status-card-top"><span className="status-icon ui-icon">UI</span><span className="status-tag planned">پوسته اولیه</span></div>
              <h4>رابط مدیریت</h4><p>React · TypeScript · RTL</p>
            </article>
            <article className="status-card">
              <div className="status-card-top"><span className="status-icon security-icon">SEC</span><span className="status-tag pending">در صف طراحی</span></div>
              <h4>هویت و مجوزها</h4><p>بدون حساب پیش‌فرض یا دسترسی آزمایشی</p>
            </article>
          </div>

          <footer className="page-footer">
            <span>CLASSMATE WORK</span>
            <span>سامانه مستقل مدیریت · {new Date().getFullYear()}</span>
          </footer>
        </section>
      </main>
    </div>
  );
}
