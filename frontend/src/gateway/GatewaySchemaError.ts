export class GatewaySchemaError extends Error {
  constructor(message = "Gateway returned an unsafe or invalid payload") {
    super(message);
    this.name = "GatewaySchemaError";
  }
}
