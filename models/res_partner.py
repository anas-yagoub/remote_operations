from odoo import models, fields, api, _
import xmlrpc.client
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class ResPartnerSync(models.Model):
    _inherit = 'res.partner'

    sent_to_remote = fields.Boolean(string="Sent to Remote", default=False)

    @api.model
    def action_send_partners_to_remote_cron(self):
        # Find all res.partner records that are not sent to remote
        partners_to_send = self.search([('sent_to_remote', '=', False)], limit=1)
        for partner in partners_to_send:
            _logger.info("Processing partner: %s", partner.name)
            partner.send_partner_to_remote()
            partner.sent_to_remote = True
            _logger.info("Done processing partner: %s", partner.name)

    def send_partner_to_remote(self):
        # Get configuration parameters
        config_parameters = self.env['ir.config_parameter'].sudo()
        url = config_parameters.get_param('remote_operations.url')
        db = config_parameters.get_param('remote_operations.db')
        username = config_parameters.get_param('remote_operations.username')
        password = config_parameters.get_param('remote_operations.password')

        # Validate settings
        if not all([url, db, username, password]):
            raise ValidationError("Remote server settings must be fully configured (URL, DB, Username, Password)")

        # Create XML-RPC connection and send data
        try:
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common', allow_none=True)
            uid = common.authenticate(db, username, password, {})
            models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object', allow_none=True)

            # Prepare contact data
            partner_data = self._prepare_partner_data(models, db, uid, password, self)

            # Check if the contact already exists in the remote database
            remote_partner_id = self._get_remote_id_if_set(
                models, db, uid, password, 'res.partner', 'name', self.name
            )
            
            if remote_partner_id:
                # Update existing contact
                models.execute_kw(db, uid, password, 'res.partner', 'write', [[remote_partner_id], partner_data])
                _logger.info("Updated partner: %s", self.name)
            else:
                # Create new contact
                new_partner_id = models.execute_kw(db, uid, password, 'res.partner', 'create', [partner_data])
                _logger.info("Created new partner: %s", self.name)

        except Exception as e:
            raise ValidationError(f"Error while sending contact data to remote server: {e}")

    def _prepare_partner_data(self, models, db, uid, password, partner):
        # Prepare data to match the remote contact fields
        account_receivable_id_to_check = self.property_account_receivable_id.code
        account_payable_to_check = self.property_account_payable_id.code

        property_account_receivable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_receivable_id_to_check)
        property_account_payable_id = self._get_remote_id(models, db, uid, password, 'account.account', 'code', account_payable_to_check)

        return {
            'name': partner.name,
            'email': partner.email,
            'phone': partner.phone,
            'is_company': partner.is_company,
            'company_type': partner.company_type,
            'mobile': partner.mobile,
            'street': partner.street,
            'street2': partner.street2,
            'city': partner.city,
            'zip': partner.zip,
            'country_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country', 'name', partner.country_id) or False,
            # 'state_id': self._get_remote_id_if_set(models, db, uid, password, 'res.country.state', 'name', partner.state_id),
            'vat': partner.vat,
            'customer_rank': partner.customer_rank,
            'supplier_rank': partner.supplier_rank,
            'property_account_receivable_id': property_account_receivable_id,
            'property_account_payable_id': property_account_payable_id,
        }
           

    
    def _get_remote_id_if_set(self, models, db, uid, password, model, field_name, field):
        if hasattr(field, 'name'):  # Check if the field is a recordset with a 'name' attribute
            return self._get_remote_id(models, db, uid, password, model, field_name, field.name)
        elif isinstance(field, str):  # Handle the case where field is a string
            return self._get_remote_id(models, db, uid, password, model, field_name, field)
        return False
    
    
    def _get_remote_id(self, models, db, uid, password, model, field_name, field_value):
        remote_record = models.execute_kw(
            db, uid, password, model, 'search_read', 
            [[(field_name, '=', field_value)]], 
            {'fields': ['id'], 'limit': 1}
        )
        if not remote_record:
            # Instead of raising an error, return None or handle creation here
            _logger.warning(
                "The record for model '%s' with %s '%s' was not found in the remote database.",
                model, field_name, field_value
            )
            return None  # Or choose to create the record dynamically if needed
        return remote_record[0]['id']
