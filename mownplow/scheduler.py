import logging
import queue

class PQueue(queue.PriorityQueue):
    def peek(self):
        """
        Return the highest-priority item in the queue without removing it.

        :return: The highest-priority item in the queue.
        :raises queue.Empty: If the priority queue is empty.
        """
        try:
            with self.mutex:
                return self.queue[0]
        except IndexError:
            raise queue.Empty


class PlowScheduler:
    # Manage plow priorities

    def __init__(self):
        self.dest_queue = PQueue()
        self.dest_priorities = {}

    def add_dest_priority(self, dest: str, priority: int):
        logging.debug(f"Adding Dest: {dest} - Priority: {priority} to schedule")
        self.dest_priorities[dest] = priority
        self.dest_queue.put((priority, dest))

    def get_current_priority(self):
        try:
            priority, dest = self.dest_queue.peek()
            return dest
        except IndexError:
            logging.error("The priority queue is empty")
            return None

    def remove_current_priority(self):
        priority, dest = self.dest_queue.get()
        return dest

    def add_dest_to_q(self, dest: str):
        self.dest_queue.put((self.dest_priorities[dest], dest))

    def rem_dest_from_priorities(self, dest: str):
        self.dest_priorities.pop(dest, None)
