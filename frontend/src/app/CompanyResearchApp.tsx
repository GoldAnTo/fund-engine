import { BrowserRouter } from "react-router-dom";
import { CompanyResearchRoutes } from "./CompanyResearchRoutes";
import "../styles/company-research.css";

export default function CompanyResearchApp() {
  return <BrowserRouter><CompanyResearchRoutes /></BrowserRouter>;
}
