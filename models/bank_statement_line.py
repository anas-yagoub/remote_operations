# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging
_logger = logging.getLogger(__name__)

class AccountMoveCustom(models.Model):

    _name = 'bank.statement.line.custom'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name"
    _order = "id desc"
    
    name = fields.Char(string="Name")
    payment_ref = fields.Char('Payment Reference')
    date = fields.Date(string="Date")
    partner_id = fields.Many2one('res.partner', string="Customer")
    currency_id = fields.Many2one('res.currency', string="Currency")
    amount = fields.Monetary(string='Amount')
    ref = fields.Char('Ref')
    narration = fields.Html('Terms and Conditions')
    company_id = fields.Many2one('res.company', string="Company")
    journal_id = fields.Many2one('account.journal', string="Journal")
    destination_journal_id = fields.Many2one('account.journal', string="Destination Journal")
    custom_state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('created', 'Created'),
        ('rejected', 'Rejected'),
        ('cancel', 'Cancel'),
    ], default="draft", string="Custom State")
    statement_id = fields.Many2one('account.bank.statement.line', string="Account Statement", readonly=True)
    payment_type = fields.Selection(selection=[
        ('outbound', 'Send'),
        ('inbound', 'Receive'),
    ], string="Payment Type")
    
    statement_count = fields.Integer(string="Statement Count", compute="_compute_statement_count")
    source_state = fields.Selection([
        ('draft', 'Draft'),
        ('in_process','In Process'),
        ('paid','Paid'),
        ('posted','Posted'),
        ('canceled','Canceled'),
        ('rejected', 'Rejected'),
        ('delete', 'Deleted'),
        ('edit', 'edited'),
    ], string='Source State', tracking=True)
    
    def _compute_statement_count(self):
        obj = self.env['account.bank.statement.line']
        for rec in self:
            rec.statement_count = obj.search_count([('statement_custom_id', '=', rec.id)])
            
    def open_statements(self):
        return {
            'name': _('Statement Records'),
            'domain': [('statement_custom_id', '=', self.id)],
            'view_type': 'form',
            'res_model': 'account.bank.statement.line',
            'view_id': False,
            'view_mode': 'list,form',
            'type': 'ir.actions.act_window',
            } 
    
    def button_rejected(self):
        for rec in self:
            rec.write({
                'custom_state': 'rejected',
            })
    
    def button_draft(self):
        for rec in self:
            rec.write({
                'custom_state': 'draft',
            })
    
    def button_created(self):
        for rec in self:
            rec.write({
                'custom_state': 'created',
            })
            
            
    def reconcile_internal_transfer_payment(self):
        """Create and reconcile outbound and inbound statement lines for internal transfer."""
        for rec in self:
            # Fetch company liquidity transfer account
            transfer_liquidity_account = rec.company_id.transfer_account_id
            if not transfer_liquidity_account:
                raise ValidationError(_("Liquidity Transfer Account is not configured in the company settings!"))
            transfer_liquidity_account_id = transfer_liquidity_account.id
            _logger.info("Transfer Liquidity Account ID: %s", transfer_liquidity_account_id)

            # Create outbound statement line (money leaving source journal)
            outbound_statement_vals = {
                'journal_id': rec.journal_id.id,
                'currency_id': rec.currency_id.id or None,
                'amount': -rec.amount,  # Negative for outflow
                'date': rec.date,
                'payment_ref': f"Internal Transfer to {rec.destination_journal_id.name}",
                'company_id': rec.company_id.id or None,
                'ref': rec.payment_ref,
                'statement_custom_id': rec.id,
            }
            _logger.info("Outbound Payment Data: %s", outbound_statement_vals)
            outbound_statement = self.env['account.bank.statement.line'].create(outbound_statement_vals)
            outbound_statement_id = outbound_statement.id
            _logger.info("Outbound Payment Created: %s", outbound_statement_id)

            # Create inbound statement line (money entering destination journal)
            inbound_statement_vals = {
                'journal_id': rec.destination_journal_id.id,
                'currency_id': rec.currency_id.id or None,
                'amount': rec.amount,  # Positive for inflow
                'date': rec.date,
                'payment_ref': f"Internal Transfer from {rec.journal_id.name}",
                'company_id': rec.company_id.id or None,
                'ref': rec.payment_ref,
                'statement_custom_id': rec.id,

            }
            _logger.info("Inbound Payment Data: %s", inbound_statement_vals)
            inbound_statement = self.env['account.bank.statement.line'].create(inbound_statement_vals)
            inbound_statement_id = inbound_statement.id
            _logger.info("Inbound Payment Created: %s", inbound_statement_id)

            # Process outbound statement line
            if not outbound_statement.exists():
                raise ValidationError(_("Outbound payment %s does not exist!") % outbound_statement_id)

            move_id = outbound_statement.move_id.id
            journal_id = outbound_statement.journal_id.id
            if not move_id or not journal_id:
                raise ValidationError(_("Outbound statement line %s has no move or journal!") % outbound_statement_id)

            journal = self.env['account.journal'].browse(journal_id)
            suspense_account_id = journal.suspense_account_id.id
            journal_name = journal.name
            if not suspense_account_id:
                raise ValidationError(_("No suspense account defined for journal %s!") % journal_name)

            suspense_lines = self.env['account.move.line'].search([
                ('move_id', '=', move_id),
                ('account_id', '=', suspense_account_id),
            ])
            if not suspense_lines:
                raise ValidationError(_("No suspense line found in move for outbound payment %s!") % outbound_statement_id)

            suspense_line_id = suspense_lines[0].id
            outbound_move = self.env['account.move'].browse(move_id)
            try:
                outbound_move.button_draft()
                _logger.info("Outbound move %s set to draft", move_id)
            except Exception as e:
                _logger.warning("button_draft failed for move %s: %s. Proceeding anyway.", move_id, str(e))

            self.env['account.move.line'].browse(suspense_line_id).write({
                'account_id': transfer_liquidity_account_id,
                'name': f"Internal Transfer to {rec.destination_journal_id.name}",
            })
            _logger.info("Outbound suspense line %s updated to transfer account %s", suspense_line_id, transfer_liquidity_account_id)

            try:
                outbound_move.action_post()
                _logger.info("Outbound move %s posted", move_id)
            except Exception as e:
                _logger.error("Failed to post outbound move %s: %s", move_id, str(e))
                raise

            # Process inbound statement line
            if not inbound_statement.exists():
                raise ValidationError(_("Inbound payment %s does not exist!") % inbound_statement_id)

            move_id = inbound_statement.move_id.id
            journal_id = inbound_statement.journal_id.id
            if not move_id or not journal_id:
                raise ValidationError(_("Inbound statement line %s has no move or journal!") % inbound_statement_id)

            journal = self.env['account.journal'].browse(journal_id)
            suspense_account_id = journal.suspense_account_id.id
            journal_name = journal.name
            if not suspense_account_id:
                raise ValidationError(_("No suspense account defined for journal %s!") % journal_name)

            suspense_lines = self.env['account.move.line'].search([
                ('move_id', '=', move_id),
                ('account_id', '=', suspense_account_id),
            ])
            if not suspense_lines:
                raise ValidationError(_("No suspense line found in move for inbound payment %s!") % inbound_statement_id)

            suspense_line_id = suspense_lines[0].id
            inbound_move = self.env['account.move'].browse(move_id)
            try:
                inbound_move.button_draft()
                _logger.info("Inbound move %s set to draft", move_id)
            except Exception as e:
                _logger.warning("button_draft failed for move %s: %s. Proceeding anyway.", move_id, str(e))

            self.env['account.move.line'].browse(suspense_line_id).write({
                'account_id': transfer_liquidity_account_id,
                'name': f"Internal Transfer from {rec.journal_id.name}",
            })
            _logger.info("Inbound suspense line %s updated to transfer account %s", suspense_line_id, transfer_liquidity_account_id)

            try:
                inbound_move.action_post()
                _logger.info("Inbound move %s posted", move_id)
            except Exception as e:
                _logger.error("Failed to post inbound move %s: %s", move_id, str(e))
                raise

            # Reconcile the moves
            lines_to_reconcile = self.env['account.move.line'].search([
                ('account_id', '=', transfer_liquidity_account_id),
                ('move_id', 'in', [outbound_move.id, inbound_move.id]),
                ('reconciled', '=', False),
            ])
            if lines_to_reconcile:
                try:
                    lines_to_reconcile.reconcile()
                    _logger.info("Reconciled lines for transfer account %s: Outbound %s - Inbound %s",
                                 transfer_liquidity_account_id, outbound_statement_id, inbound_statement_id)
                except Exception as e:
                    _logger.error("Failed to reconcile lines: %s", str(e))
                    raise ValidationError(_("Failed to reconcile transfer: %s") % str(e))

            _logger.info("Payments reconciled: Outbound %s - Inbound %s", outbound_statement_id, inbound_statement_id)
            rec.write({
                'custom_state': 'created',
            })
            

    
    