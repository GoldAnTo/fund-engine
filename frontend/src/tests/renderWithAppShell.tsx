import { ReactElement } from "react";
import { render, RenderOptions } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";

interface Options extends RenderOptions {
  initialEntries?: string[];
}

// Renders an element inside the AppShell layout route with a MemoryRouter.
// Pages that depend on react-router hooks (useSearchParams / useParams) must
// be wrapped like this so they resolve their routing context.
export function renderWithAppShell(
  ui: ReactElement,
  { initialEntries = ["/"], ...rest }: Options = {}
) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="*" element={ui} />
        </Route>
      </Routes>
    </MemoryRouter>,
    rest
  );
}