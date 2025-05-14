from odoo import models, fields, api, _

class AccountPaymentCustom(models.Model):
    _name = 'account.payment.custom'
    _description = 'Custom Account Payment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = "name"
    _order = "id desc"


    name = fields.Char(string="Name")
    payment_type = fields.Selection([
        ('inbound', 'Receive'),
        ('outbound', 'Send'),
    ], string='Payment Type')
    partner_type = fields.Selection([
        ('customer', 'Customer'),
        ('supplier', 'Vendor'),
    ], string='Partner Type')
    partner_id = fields.Many2one('res.partner', string='Partner')
    amount = fields.Monetary(string='Amount')
    currency_id = fields.Many2one('res.currency', string='Currency')
    date = fields.Date(string='Date')
    journal_id = fields.Many2one('account.journal', string='Journal')
    memo = fields.Char('Memo')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_process','In Process'),
        ('paid','Paid'),
        ('posted','Posted'),
        ('canceled','Canceled'),
        ('rejected', 'Rejected'),
    ], string='State')
    payment_method_line_id = fields.Many2one('account.payment.method.line', string='Payment Method')
    custom_state = fields.Selection(selection=[
        ('draft', 'Draft'),
        ('created', 'Created'),
        ('rejected', 'Rejected'),
        ('cancel', 'Cancel'),
    ], default="draft", string="Custom State")
    payment_id = fields.Many2one('account.payment', string="Related Account Payment", readonly=True)
    company_id = fields.Many2one('res.company', string="Company")
    payment_count = fields.Integer(string="Payment Count", compute="_compute_payment_count")
    
    def _compute_payment_count(self):
        obj = self.env['account.payment']
        for rec in self:
            rec.payment_count = obj.search_count([('payment_custom_id', '=', rec.id)])
            
    def open_payments(self):
        return {
            'name': _('Payment Records'),
            'domain': [('payment_custom_id', '=', self.id)],
            'view_type': 'form',
            'res_model': 'account.payment',
            'view_id': False,
            'view_mode': 'list,form',
            'type': 'ir.actions.act_window',
            } 
    
    def button_draft(self):
        for rec in self:
            rec.write({
                'custom_state': 'draft',
            })

    def button_rejected(self):
        for rec in self:
            rec.write({
                'custom_state': 'rejected',
            })
    
    def button_created(self):
        for rec in self:
            rec.write({
                'custom_state': 'created',
            })
    
    
    def action_create_payment(self):
        for rec in self:
            payment_vals = {
                'partner_id': rec.partner_id.id,
                'payment_type': rec.payment_type, 
                'partner_type': rec.partner_type,
                'currency_id': rec.currency_id.id,
                'memo': rec.memo,  
                'date': rec.date,
                'journal_id': rec.journal_id.id,
                'payment_custom_id': rec.id,
                'company_id': rec.company_id.id,
                'amount': rec.amount,
            }
            payment = self.env['account.payment'].create(payment_vals)
            payment.action_post()
            rec.payment_id = payment.id
            rec.write({
                'custom_state': 'created',
            })
     

