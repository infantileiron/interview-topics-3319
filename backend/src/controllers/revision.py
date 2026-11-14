from src.services import revision as service
def submit(data,user):
 batch,created=service.submit_batch(data,user)
 return {'created':created,'batch':batch}
def detail(batch_id):return service.get_batch(batch_id)
def list_batches(family_id=None):return service.list_batches(family_id)
