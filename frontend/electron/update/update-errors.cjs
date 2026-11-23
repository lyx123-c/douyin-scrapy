class UpdateProtocolError extends Error {
  constructor(message, code = 'UPDATE_PROTOCOL_ERROR', details = null) {
    super(message)
    this.name = 'UpdateProtocolError'
    this.code = code
    this.details = details
  }
}

module.exports = {
  UpdateProtocolError,
}
