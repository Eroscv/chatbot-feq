# Relatório de Testes — Chatbot Máximo

Suite completa de testes unitários e de integração do fluxo de qualificação de leads.

**Resultado final: 85 testes — 100% passando**

---

## Suites

| Arquivo | Testes | Descrição |
|---------|--------|-----------|
| `test_all.py` | 56 | Testes unitários por módulo |
| `test_qualification_flow.py` | 29 | Integração do fluxo completo |
| **Total** | **85** | **100% passando** |

---

## test_all.py — Testes Unitários (56)

### TestDedup — 6 testes
| Teste | O que verifica |
|-------|---------------|
| `test_first_time_returns_false` | Primeira vez visto retorna False |
| `test_second_time_returns_true` | Mesma mensagem retorna True (duplicata bloqueada) |
| `test_different_ids_independent` | IDs diferentes são independentes |
| `test_empty_id_never_blocks` | ID vazio nunca bloqueia |
| `test_ttl_expiry` | Após TTL, ID é aceito novamente |
| `test_size_grows_and_shrinks` | Cache cresce e é limpa corretamente |

### TestRateLimiter — 5 testes
| Teste | O que verifica |
|-------|---------------|
| `test_burst_allowed` | Burst de 3 mensagens é permitido |
| `test_exceeds_burst_blocked` | 4ª mensagem é bloqueada |
| `test_different_numbers_independent` | Números diferentes têm buckets isolados |
| `test_refill_over_time` | Tokens são reabastecidos com o tempo |
| `test_size_tracked` | Tamanho do pool é rastreado |

### TestLogSanitizer — 6 testes
| Teste | O que verifica |
|-------|---------------|
| `test_phone_masked` | Telefone brasileiro mascarado (prefixo mantido) |
| `test_cnpj_masked` | CNPJ mascarado |
| `test_cpf_masked` | CPF mascarado |
| `test_email_masked` | E-mail mascarado (domínio mantido) |
| `test_plain_text_unchanged` | Texto sem dados sensíveis não alterado |
| `test_multiple_patterns_in_one_string` | Múltiplos padrões na mesma string |

### TestDatabase — 10 testes
| Teste | O que verifica |
|-------|---------------|
| `test_upsert_lead_insert` | Inserção de lead com ciclo mapeado |
| `test_upsert_lead_update` | Atualização preserva card_id |
| `test_upsert_preserves_existing_fields` | COALESCE preserva campos com None |
| `test_log_message_in` | Log de mensagem recebida |
| `test_log_message_out` | Log de mensagem enviada |
| `test_get_all_leads_no_filter` | Lista todos os leads |
| `test_get_all_leads_state_filter` | Filtro por state funciona |
| `test_stats` | Estatísticas por serviço e ciclo |
| `test_get_nonexistent_lead` | Retorna None para lead inexistente |
| `test_conversation_chronological_order` | Ordem cronológica no histórico |

### TestHandoff — 7 testes
| Teste | O que verifica |
|-------|---------------|
| `test_take_registers` | Take registra número |
| `test_is_human_false_by_default` | Número novo não está em handoff |
| `test_release_removes` | Release remove do handoff |
| `test_release_nonexistent_safe` | Release de número inexistente não falha |
| `test_list_all` | Lista todos em handoff |
| `test_get_status_returns_entry` | Retorna dados do atendente |
| `test_get_status_none_when_bot` | Retorna None quando bot atende |

### TestWebhookParser — 6 testes
| Teste | O que verifica |
|-------|---------------|
| `test_parses_text_message` | Parseia mensagem de texto |
| `test_ignores_from_me` | Ignora mensagens do próprio bot |
| `test_parses_button` | Parseia botão com selected_id e título |
| `test_empty_payload` | Payload vazio retorna lista vazia |
| `test_chat_id_preserved` | chat_id é preservado |
| `test_from_name_extracted` | Nome extraído de _vname/pushname |

### TestMessageBuffer — 4 testes
| Teste | O que verifica |
|-------|---------------|
| `test_texts_concatenated` | Textos rápidos são concatenados em 1 |
| `test_button_wins_over_text` | Botão tem prioridade sobre texto |
| `test_different_numbers_independent` | Números têm filas independentes |
| `test_timer_reset_on_new_msg` | Nova msg reinicia o timer de flush |

### TestConfig — 1 teste
| Teste | O que verifica |
|-------|---------------|
| `test_loads_from_env` | Todas as variáveis presentes, MATON_API_KEY sem URL contaminada |

### TestHandlerStateMachine — 10 testes
| Teste | O que verifica |
|-------|---------------|
| `test_new_lead_creates_card` | Primeira mensagem cria card no CRM |
| `test_state_advances_to_welcome` | Estado avança para welcome |
| `test_button_contratar_advances_to_pj` | Botão id1 → state=pj |
| `test_button_emprego_concludes` | Botão id2 → state removido (concluído) |
| `test_handoff_silences_bot` | Handoff manual impede resposta |
| `test_wrong_stage_silences_bot` | Stage errado no CRM impede resposta |
| `test_human_last_message_silences_bot` | Humano no app impede resposta |
| `test_pj_sim_advances_to_servicos` | Botão Sim → state=servicos |
| `test_servico_chosen_advances_to_cidade` | Serviço escolhido → state=cidade |
| `test_cidade_text_advances_to_cnpj` | Texto cidade → state=cnpj |

---

## test_qualification_flow.py — Testes de Integração (29)

### TestFluxoCompletoWhatsApp — 11 testes
Simula conversa completa: `new → welcome → pj → servicos → cidade → cnpj → canal → concluido`

| Teste | O que verifica |
|-------|---------------|
| `test_etapa1_boas_vindas_cria_card` | Etapa 1: card criado no CRM com card_id no contexto |
| `test_etapa1_salva_lead_no_banco` | Etapa 1: lead persistido no SQLite com ciclo=1 |
| `test_etapa2_contratar_vai_para_pj` | Etapa 2: botão id1 → state=pj |
| `test_etapa3_pj_sim_vai_para_servicos` | Etapa 3: botão Sim → state=servicos |
| `test_etapa4_escolhe_servico_vai_para_cidade` | Etapa 4: serviço salvo e state=cidade |
| `test_etapa5_cidade_vai_para_cnpj` | Etapa 5: cidade salva e state=cnpj |
| `test_etapa6_cnpj_vai_para_canal` | Etapa 6: CNPJ salvo e state=canal |
| `test_etapa7_whatsapp_conclui_fluxo` | Etapa 7: lead concluído com todos os campos |
| `test_etapa7_crm_atualizado_em_cada_etapa` | CRM update_card chamado ≥5 vezes |
| `test_historico_completo_salvo` | Msgs in/out salvas no SQLite |
| `test_from_name_salvo_no_historico` | from_name correto (lead/Bot) por direção |

### TestFluxoCompletoEmail — 2 testes
| Teste | O que verifica |
|-------|---------------|
| `test_canal_email_pede_endereco` | Botão E-mail → state=aguarda_email |
| `test_email_digitado_conclui_fluxo` | E-mail salvo, state=concluido |

### TestFluxosDesvio — 8 testes
| Teste | O que verifica |
|-------|---------------|
| `test_emprego_conclui_sem_qualificacao` | Emprego → estado removido, interesse=emprego |
| `test_emprego_move_card_para_stage_emprego` | Card movido para stage correto |
| `test_nao_pj_conclui_com_mensagem_encerramento` | Não PJ → encerramento sem qualificação |
| `test_todos_servicos_sao_aceitos` | Todos os 5 serviços (id1-id5) aceitos |
| `test_mensagem_duplicada_ignorada` | Dedup bloqueia segundo message_id igual |
| `test_lead_reinicia_se_estado_new` | Sem estado → começa do zero |
| `test_texto_ignorado_em_estado_servicos` | Texto em estado de botão ignorado silenciosamente |
| `test_card_nao_criado_duas_vezes` | Re-envio não recria card existente |

### TestFluxoHandoff — 5 testes
| Teste | O que verifica |
|-------|---------------|
| `test_handoff_manual_silencia_bot` | Bot não responde após take() |
| `test_handoff_release_retoma_bot` | Bot responde após release() |
| `test_stage_errado_silencia_bot` | Stage ≠ bot_stage → silencia |
| `test_humano_no_chat_silencia_bot` | source=web → silencia |
| `test_handoff_loga_mensagens_mesmo_silenciado` | Msgs recebidas são logadas mesmo em handoff |

### TestMultiplosLeads — 3 testes
| Teste | O que verifica |
|-------|---------------|
| `test_estados_independentes_por_numero` | 3 números em etapas diferentes simultaneamente |
| `test_handoff_de_um_nao_afeta_outro` | Handoff de p1 não silencia p2 |
| `test_banco_registra_leads_separados` | 5 leads em registros independentes no banco |

---

## Bugs encontrados e corrigidos pelos testes

| # | Severidade | Arquivo | Bug | Correção |
|---|-----------|---------|-----|----------|
| 1 | 🔴 Alta | `database.py` | `COALESCE` com string vazia sobrescrevia campos existentes no banco | Passa `None` para campos ausentes no ctx |
| 2 | 🔴 Alta | `webhook_parser.py` | `from_name` sempre retornava o número (campo `_vname`/`pushname` não consultado) | Fallback em 4 campos: `_vname → pushname → from_name → contact.name` |
| 3 | 🔴 Alta | `handler.py` | `UnboundLocalError` em `_handle_welcome` quando card já existe (`new_ctx` não definido no branch `already_exists=True`) | Define `new_ctx` antes do branch e preserva estado avançado |
| 4 | 🟠 Média | `handler.py` | Texto livre enviado em estados que aguardam botão (`pj`, `servicos`, `canal`) resetava o fluxo para `welcome` | Ignora silenciosamente textos em estados de botão |

---

## Executar

```bash
cd chatbot_maximo
source .venv/bin/activate

# Suite completa
python -m pytest tests/ -v

# Só unitários
python -m pytest tests/test_all.py -v

# Só fluxo de qualificação
python -m pytest tests/test_qualification_flow.py -v
```
