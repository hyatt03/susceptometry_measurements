import socketio, asyncio
from models import db, Session, ExperimentStep, DataPoint

from peewee import DoesNotExist
from playhouse.shortcuts import model_to_dict


# Class containing events relevant for all different namespaces (used as a baseclass
class UniversalEvents(socketio.AsyncNamespace):
    # Init all the self variables needed
    def __init__(self, namespace=None):
        super().__init__(namespace)
        self.cryo_namespace = None
        self.magnetism_namespace = None
        self.browser_namespace = None
        self.steps_done = []

    # Basically an init function to allow communication between the channels
    def set_namespaces(self, cryo, magnetism, browser):
        self.cryo_namespace = cryo
        self.magnetism_namespace = magnetism
        self.browser_namespace = browser

    # Ask for idn on connect
    async def on_connect(self, sid, environ):
        print('Got a connection from ', sid)

    # Tell the user that a client disconnected
    def on_disconnect(self, sid):
        with db.connection_context():
            try:
                session = Session.get(Session.sid == sid)
                print(session.type, 'disconnected')
            except Session.DoesNotExist:
                print('disconnect ', sid)

    # Allow clients to identify themselves
    async def on_idn(self, sid, data):
        # Figure out what type of client we have
        client_type = data.split('_')[0]
        is_old = 'Old'

        # Connect to the database
        with db.connection_context():
            # Register sid with session
            try:
                # First check if the session exists, then we update
                Session.get(Session.idn == data).update(sid=sid).where(Session.idn == data).execute()
            except Session.DoesNotExist:
                # if it does not exist, we just create it
                Session.create(idn=data, sid=sid, type=client_type).save()
                is_old = 'New'

        print(client_type)

        print(f'{is_old} client connected with idn: {data}, and sid: {sid}')

    # Sends event to retrieve queue size
    # No abstract handler, should be handled in the individual classes
    async def get_queue_size(self):
        await self.emit('get_queue_size')

    # Define a method to check if a step is done
    def is_step_done(self, step_id):
        return step_id in self.steps_done

    # And an endpoint to mark a step as done
    async def on_mark_step_as_done(self, sid, step):
        # Add the id of the step to the done list
        step_id = step['id']
        self.steps_done.append(step_id)

        # Check if the step is done both places
        if self.magnetism_namespace.is_step_done(step_id) and self.cryo_namespace.is_step_done(step_id):
            # Both are done, so we should mark it as done in the database
            with db.connection_context():
                ExperimentStep.update(step_done=True).where(ExperimentStep.id == step_id).execute()
            
            # Next we should push the next step to the clients (if applicable)
            await self.browser_namespace.push_next_step_to_clients()

    # This method is implemented in the individual clients, where relevant
    async def push_next_step(self, step):
        pass

    async def get_next_step(self):
        with db.connection_context():
            try:
                # Get the next step
                step = ExperimentStep.select().where(ExperimentStep.step_done == False).order_by(
                    ExperimentStep.id).first()

                # Check if the step is none, and skip to the catch clause if it is
                if step is None:
                    raise DoesNotExist('Step does not exist')

                # Check if the step has an associated datapoint
                if DataPoint.select().where(ExperimentStep == step).count() < 1:
                    step.generate_datapoint()

                # Convert step to dict
                step_d = model_to_dict(step)

                # Set the experiment id (different from the step id)
                step_d['experiment_configuration_id'] = step_d['experiment_configuration']['id']

                # Remove datetime and experiment configuration from the dict
                # They are not needed in the client, and they are not directly serializable to json (due to missing datetime format)
                del (step_d['created'])
                del (step_d['experiment_configuration'])

                # Return the step if it exists
                return step_d
            # Check if the step even exists
            except DoesNotExist:
                # It is OK if it does not exist, we should just stop measuring
                print('No more steps ready')

                # Return None if no step exists
                return None

    async def on_get_latest_step(self, sid):
        # We grab the latest step, where it isn't marked as done, and send it
        step = await self.get_next_step()

        # Push it to the client
        if step is not None:
            print('Pushing latest step to client')

            await self.push_next_step(step)

