import { useState, type ReactElement } from "react";
import { BrowserRouter } from "react-router-dom";

import { HttpGatewayClient } from "@/gateway/HttpGatewayClient";

import { GatewayRoutes } from "./GatewayRoutes";

export function FundClawApp(): ReactElement {
  const [client] = useState(() => new HttpGatewayClient());

  return (
    <BrowserRouter>
      <GatewayRoutes client={client} />
    </BrowserRouter>
  );
}
