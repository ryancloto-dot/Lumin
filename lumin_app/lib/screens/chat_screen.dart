import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../models/chat.dart';
import '../providers/lumin_provider.dart';
import '../theme.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _send(LuminProvider provider) {
    final text = _controller.text.trim();
    if (text.isEmpty || provider.isSendingChat) {
      return;
    }
    _controller.clear();
    provider.sendChatMessage(text);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent + 140,
          duration: const Duration(milliseconds: 260),
          curve: Curves.easeOutCubic,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        final messages = provider.chatMessages;
        return SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 12),
                child: Row(
                  children: [
                    Text('Chat', style: Theme.of(context).textTheme.headlineSmall),
                    const Spacer(),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      decoration: BoxDecoration(
                        color: LuminColors.card,
                        borderRadius: BorderRadius.circular(999),
                        border: Border.all(color: LuminColors.border),
                      ),
                      child: Text(provider.isOnline ? 'Connected' : 'Offline'),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: Container(
                  margin: const EdgeInsets.symmetric(horizontal: 16),
                  decoration: BoxDecoration(
                    color: LuminColors.card,
                    borderRadius: BorderRadius.circular(24),
                    border: Border.all(color: LuminColors.border),
                  ),
                  child: messages.isEmpty
                      ? Center(
                          child: Padding(
                            padding: const EdgeInsets.all(32),
                            child: Text(
                              'Send a message to your NanoClaw/Lumin stack from your phone.',
                              textAlign: TextAlign.center,
                              style: Theme.of(context).textTheme.bodyLarge,
                            ),
                          ),
                        )
                      : ListView.builder(
                          controller: _scrollController,
                          padding: const EdgeInsets.all(16),
                          itemCount: messages.length,
                          itemBuilder: (context, index) => Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: _ChatBubble(message: messages[index]),
                          ),
                        ),
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _controller,
                        minLines: 1,
                        maxLines: 5,
                        textInputAction: TextInputAction.send,
                        onSubmitted: (_) => _send(provider),
                        decoration: const InputDecoration(
                          hintText: 'Tell NanoClaw what to do...',
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    ElevatedButton(
                      onPressed: provider.isSendingChat ? null : () => _send(provider),
                      child: provider.isSendingChat
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.send_rounded),
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _ChatBubble extends StatelessWidget {
  const _ChatBubble({required this.message});

  final ChatMessageItem message;

  @override
  Widget build(BuildContext context) {
    final isUser = message.isUser;
    final bubbleColor = isUser ? LuminColors.primary.withValues(alpha: 0.18) : const Color(0xFF0F2A1D);
    final align = isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start;
    final radius = BorderRadius.circular(22);
    final timestamp = DateFormat('h:mm a').format(message.timestamp);

    return Column(
      crossAxisAlignment: align,
      children: [
        Container(
          constraints: const BoxConstraints(maxWidth: 620),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: bubbleColor,
            borderRadius: radius,
            border: Border.all(color: LuminColors.border),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (!isUser)
                        MarkdownBody(
                  data: message.text,
                  selectable: true,
                  styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
                    p: Theme.of(context).textTheme.bodyMedium,
                    codeblockDecoration: BoxDecoration(
                      color: const Color(0xFF08140E),
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: LuminColors.border),
                    ),
                  ),
                )
              else
                Text(message.text),
                      if (!isUser && message.savings != null) ...[
                        const SizedBox(height: 12),
                        Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    _MetaPill(
                      icon: Icons.savings_outlined,
                      label:
                          'Saved ${message.savings!.savingsPct.toStringAsFixed(1)}% • ${NumberFormat.currency(symbol: '\$', decimalDigits: 4).format(message.savings!.dollarsSaved)}',
                    ),
                    if (message.modelUsed != null)
                      _MetaPill(icon: Icons.memory_rounded, label: message.modelUsed!),
                          ],
                        ),
                      ],
                      if (!isUser && message.modelUsed == 'offline') ...[
                        const SizedBox(height: 12),
                        Text(
                          'Your computer or Lumin server is offline right now.',
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.orangeAccent),
                        ),
                      ],
                    ],
                  ),
                ),
        const SizedBox(height: 6),
        Text(timestamp, style: Theme.of(context).textTheme.bodySmall),
      ],
    );
  }
}

class _MetaPill extends StatelessWidget {
  const _MetaPill({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: LuminColors.background,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: LuminColors.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: LuminColors.primary),
          const SizedBox(width: 6),
          Text(label, style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}
