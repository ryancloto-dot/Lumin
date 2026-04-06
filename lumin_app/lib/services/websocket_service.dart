import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

class WebSocketService {
  WebSocketService({
    required this.baseUrl,
    required this.apiKey,
  });

  final String baseUrl;
  final String apiKey;
  final StreamController<Map<String, dynamic>> _controller = StreamController.broadcast();

  WebSocketChannel? _channel;
  Timer? _reconnectTimer;
  bool _disposed = false;
  bool _connected = false;

  Stream<Map<String, dynamic>> get stream => _controller.stream;
  bool get isConnected => _connected;

  void connect() {
    if (_disposed) {
      return;
    }
    disconnect();
    final normalized = baseUrl.replaceAll(RegExp(r'^http'), 'ws').replaceAll(RegExp(r'/$'), '');
    final uri = Uri.parse('$normalized/ws/live?key=${Uri.encodeQueryComponent(apiKey)}');
    try {
      _channel = WebSocketChannel.connect(uri);
      _connected = true;
      _channel!.stream.listen(
        (message) {
          if (message is String) {
            final data = jsonDecode(message) as Map<String, dynamic>;
            _controller.add(data);
          }
        },
        onError: (_) => _scheduleReconnect(),
        onDone: _scheduleReconnect,
      );
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    _connected = false;
    if (_disposed) {
      return;
    }
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 3), connect);
  }

  void disconnect() {
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _channel = null;
    _connected = false;
  }

  void dispose() {
    _disposed = true;
    disconnect();
    _controller.close();
  }
}
